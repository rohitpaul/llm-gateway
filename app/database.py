"""SQLite database layer for the LLM gateway proxy.

Provides async access to three tables:
  - virtual_keys: API key management with usage tracking and filters.
  - requests:    Per-request usage logging with full metadata.
  - daily_usage: Pre-aggregated daily stats per key/model/provider.

Uses aiosqlite for non-blocking I/O and sqlite3.Row for dict-like results.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from app import config

# ---------------------------------------------------------------------------
# Schema versioning & migrations
# ---------------------------------------------------------------------------

# Current schema version — bump when adding migrations.
SCHEMA_VERSION = 3

_CREATE_SCHEMA_META = """
CREATE TABLE IF NOT EXISTS _schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# v1: Initial schema — the three core tables + indexes.
# Tables are created with IF NOT EXISTS so fresh DBs and pre-existing DBs
# both converge safely.
_MIGRATIONS: dict[int, list[str]] = {
    1: [
        # -- virtual_keys --
        """
        CREATE TABLE IF NOT EXISTS virtual_keys (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL,
            key_hash        TEXT    NOT NULL UNIQUE,
            key_prefix      TEXT    NOT NULL,
            provider_filter TEXT,
            model_filter    TEXT,
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            last_used_at    TEXT,
            request_count   INTEGER NOT NULL DEFAULT 0,
            token_limit     INTEGER,
            tokens_used     INTEGER NOT NULL DEFAULT 0
        );
        """,
        # -- requests --
        """
        CREATE TABLE IF NOT EXISTS requests (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            virtual_key_id    INTEGER,
            request_id        TEXT    NOT NULL,
            model             TEXT    NOT NULL,
            provider          TEXT    NOT NULL,
            input_tokens      INTEGER NOT NULL DEFAULT 0,
            output_tokens     INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            cost              REAL    NOT NULL DEFAULT 0.0,
            latency_ms        REAL    NOT NULL DEFAULT 0.0,
            status            TEXT    NOT NULL DEFAULT 'success',
            error_message     TEXT,
            source_ip         TEXT,
            request_body      TEXT,
            response_body     TEXT,
            created_at        TEXT    NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            FOREIGN KEY (virtual_key_id) REFERENCES virtual_keys(id)
        );
        """,
        # -- daily_usage --
        """
        CREATE TABLE IF NOT EXISTS daily_usage (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            virtual_key_id  INTEGER,
            model           TEXT    NOT NULL,
            provider        TEXT    NOT NULL,
            request_count   INTEGER NOT NULL DEFAULT 0,
            input_tokens    INTEGER NOT NULL DEFAULT 0,
            output_tokens   INTEGER NOT NULL DEFAULT 0,
            cost            REAL    NOT NULL DEFAULT 0.0,
            UNIQUE(date, virtual_key_id, model, provider)
        );
        """,
        # -- indexes --
        "CREATE INDEX IF NOT EXISTS idx_virtual_keys_key_hash ON virtual_keys(key_hash);",
        "CREATE INDEX IF NOT EXISTS idx_requests_virtual_key_id ON requests(virtual_key_id);",
        "CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at);",
        "CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model);",
        "CREATE INDEX IF NOT EXISTS idx_requests_provider ON requests(provider);",
        "CREATE INDEX IF NOT EXISTS idx_daily_usage_date ON daily_usage(date);",
        "CREATE INDEX IF NOT EXISTS idx_daily_usage_key ON daily_usage(virtual_key_id);",
    ],
    # v2: config_overrides — persist model/provider overrides in DB
    #     (avoids writing to read-only config.yaml in Docker).
    2: [
        """
        CREATE TABLE IF NOT EXISTS config_overrides (
            key       TEXT PRIMARY KEY,
            value     TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        );
        """,
    ],
    # v3: Add TTFT and TPS columns to requests table
    3: [
        "ALTER TABLE requests ADD COLUMN time_to_first_token_ms REAL;",
        "ALTER TABLE requests ADD COLUMN tokens_per_second REAL;",
        "CREATE INDEX IF NOT EXISTS idx_requests_ttft ON requests(time_to_first_token_ms);",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a sqlite3.Row to a plain dict, or return None."""
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Convert a list of sqlite3.Row objects to plain dicts."""
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------

class Database:
    """Async SQLite database interface for the LLM gateway."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or config.DEFAULT_DB_PATH
        self._db: aiosqlite.Connection | None = None

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        """Open the database connection, create meta table, run pending migrations."""
        # Guarantee parent directory exists.
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = sqlite3.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")

        # Ensure _schema_meta table exists (unversioned — always created).
        await self._db.executescript(_CREATE_SCHEMA_META)

        # Read current version from DB (0 = fresh / pre-migration DB).
        async with self._db.execute(
            "SELECT value FROM _schema_meta WHERE key = 'version'"
        ) as cur:
            row = await cur.fetchone()
        current_version = int(row["value"]) if row else 0

        # Run all migrations from current_version+1 up to SCHEMA_VERSION.
        for v in range(current_version + 1, SCHEMA_VERSION + 1):
            statements = _MIGRATIONS.get(v, [])
            for stmt in statements:
                await self._db.execute(stmt)
            await self._db.execute(
                "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES ('version', ?)",
                (str(v),),
            )
            await self._db.commit()
            print(f"  DB migration v{v} applied")

        if current_version < SCHEMA_VERSION:
            print(f"✓ DB schema: v{current_version} → v{SCHEMA_VERSION}")
        else:
            print(f"✓ DB schema: v{SCHEMA_VERSION} (up to date)")

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._db

    # -- virtual_keys --------------------------------------------------------

    async def create_key(
        self,
        name: str,
        key_hash: str,
        key_prefix: str,
        provider_filter: str | None = None,
        model_filter: str | None = None,
        token_limit: int | None = None,
    ) -> int:
        """Insert a new virtual API key. Returns the new row id."""
        async with self.db.execute(
            """
            INSERT INTO virtual_keys
                (name, key_hash, key_prefix, provider_filter, model_filter, token_limit)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, key_hash, key_prefix, provider_filter, model_filter, token_limit),
        ) as cursor:
            pass
        await self.db.commit()
        # Retrieve the id of the inserted row via a follow-up query.
        async with self.db.execute(
            "SELECT id FROM virtual_keys WHERE key_hash = ?", (key_hash,)
        ) as cur:
            row = await cur.fetchone()
            return row["id"] if row else -1

    async def validate_key(self, key_hash: str) -> dict[str, Any] | None:
        """Look up a key by its hash.

        If found and active, updates last_used_at / request_count and returns
        the key row as a dict.  Returns None otherwise.
        """
        async with self.db.execute(
            "SELECT * FROM virtual_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None

        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """
            UPDATE virtual_keys
            SET last_used_at = ?, request_count = request_count + 1
            WHERE id = ?
            """,
            (now, row["id"]),
        )
        await self.db.commit()

        # Re-fetch so the returned dict reflects the update.
        async with self.db.execute(
            "SELECT * FROM virtual_keys WHERE id = ?", (row["id"],)
        ) as cur:
            updated = await cur.fetchone()
        return _row_to_dict(updated)

    async def list_keys(self) -> list[dict[str, Any]]:
        """Return all virtual keys ordered by creation date."""
        async with self.db.execute(
            "SELECT * FROM virtual_keys ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return _rows_to_dicts(rows)

    async def deactivate_key(self, key_id: int) -> bool:
        """Set is_active=0 for the given key. Returns True if a row was updated."""
        cursor = await self.db.execute(
            "UPDATE virtual_keys SET is_active = 0 WHERE id = ?", (key_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def reactivate_key(self, key_id: int) -> bool:
        """Set is_active=1 for the given key. Returns True if a row was updated."""
        cursor = await self.db.execute(
            "UPDATE virtual_keys SET is_active = 1 WHERE id = ?", (key_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def delete_key(self, key_id: int) -> bool:
        """Delete a virtual key and all associated request history."""
        # Delete associated requests first (FK constraint)
        await self.db.execute(
            "DELETE FROM requests WHERE virtual_key_id = ?", (key_id,)
        )
        # Delete daily usage records
        await self.db.execute(
            "DELETE FROM daily_usage WHERE virtual_key_id = ?", (key_id,)
        )
        # Delete the key itself
        cursor = await self.db.execute(
            "DELETE FROM virtual_keys WHERE id = ?", (key_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    # -- requests ------------------------------------------------------------

    async def log_request(
        self,
        virtual_key_id: int | None,
        request_id: str,
        model: str,
        provider: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        cost: float = 0.0,
        latency_ms: float = 0.0,
        status: str = "success",
        error_message: str | None = None,
        source_ip: str | None = None,
        request_body: str | None = None,
        response_body: str | None = None,
        time_to_first_token_ms: float | None = None,
        tokens_per_second: float | None = None,
    ) -> int:
        """Insert a request log row. Returns the new row id."""
        # Single transaction: insert request, update key usage, upsert daily — one commit.
        async with self.db.execute(
            """
            INSERT INTO requests (
                virtual_key_id, request_id, model, provider,
                input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens,
                cost, latency_ms, status, error_message, source_ip,
                request_body, response_body,
                time_to_first_token_ms, tokens_per_second
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                virtual_key_id, request_id, model, provider,
                input_tokens, output_tokens,
                cache_read, cache_write,
                cost, latency_ms, status, error_message, source_ip,
                request_body, response_body,
                time_to_first_token_ms, tokens_per_second,
            ),
        ) as cursor:
            inserted_id = cursor.lastrowid

        # Update tokens_used on the parent key.
        if virtual_key_id is not None:
            total = input_tokens + output_tokens
            if total > 0:
                await self.db.execute(
                    "UPDATE virtual_keys SET tokens_used = tokens_used + ? WHERE id = ?",
                    (total, virtual_key_id),
                )

        # Upsert daily_usage.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await self.db.execute(
            """
            INSERT INTO daily_usage
                (date, virtual_key_id, model, provider, request_count,
                 input_tokens, output_tokens, cost)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(date, virtual_key_id, model, provider) DO UPDATE SET
                request_count = request_count + 1,
                input_tokens  = input_tokens  + excluded.input_tokens,
                output_tokens = output_tokens + excluded.output_tokens,
                cost           = cost           + excluded.cost
            """,
            (today, virtual_key_id, model, provider, input_tokens, output_tokens, cost),
        )

        # Single commit for all three operations.
        await self.db.commit()
        return inserted_id if inserted_id else -1

    async def get_requests(
        self,
        key_id: int | None = None,
        model: str | None = None,
        provider: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return a page of request logs plus total matching count.

        date_from / date_to are inclusive ISO date strings (YYYY-MM-DD or full
        ISO timestamp).  Response bodies are excluded from listing queries for
        performance.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if key_id is not None:
            conditions.append("virtual_key_id = ?")
            params.append(key_id)
        if model is not None:
            conditions.append("model = ?")
            params.append(model)
        if provider is not None:
            conditions.append("provider = ?")
            params.append(provider)
        if date_from is not None:
            conditions.append("created_at >= ?")
            params.append(date_from)
        if date_to is not None:
            conditions.append("created_at <= ?")
            params.append(date_to)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        # Total count
        count_sql = f"SELECT COUNT(*) AS cnt FROM requests {where}"
        async with self.db.execute(count_sql, params) as cur:
            row = await cur.fetchone()
        total = row["cnt"] if row else 0

        # Page of results (omit large body columns)
        data_sql = (
            f"SELECT r.id, r.virtual_key_id, vk.name as key_name, r.request_id, r.model, r.provider, "
            f"r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_tokens, "
            f"r.cost, r.latency_ms, r.status, r.error_message, r.source_ip, r.created_at, "
            f"r.time_to_first_token_ms, r.tokens_per_second "
            f"FROM requests r "
            f"LEFT JOIN virtual_keys vk ON r.virtual_key_id = vk.id "
            f"{where} ORDER BY r.created_at DESC LIMIT ? OFFSET ?"
        )
        async with self.db.execute(data_sql, params + [limit, offset]) as cur:
            rows = await cur.fetchall()

        return _rows_to_dicts(rows), total

    async def get_request(self, request_id: int) -> dict[str, Any] | None:
        """Return a single request row by its integer id, including bodies."""
        async with self.db.execute(
            "SELECT * FROM requests WHERE id = ?", (request_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_dict(row)

    # -- daily / aggregated stats -------------------------------------------

    async def get_daily_stats(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        key_id: int | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return daily_usage rows, optionally filtered by date range, key, and model."""
        conditions: list[str] = []
        params: list[Any] = []

        if date_from is not None:
            conditions.append("date >= ?")
            params.append(date_from)
        if date_to is not None:
            conditions.append("date <= ?")
            params.append(date_to)
        if key_id is not None:
            conditions.append("virtual_key_id = ?")
            params.append(key_id)
        if model is not None:
            conditions.append("model = ?")
            params.append(model)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        sql = f"SELECT * FROM daily_usage {where} ORDER BY date DESC"
        async with self.db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return _rows_to_dicts(rows)

    async def get_hourly_stats(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        key_id: int | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate requests by hour from the raw requests table."""
        conditions: list[str] = ["status = 'success'"]
        params: list[Any] = []

        if date_from is not None:
            conditions.append("DATE(created_at) >= ?")
            params.append(date_from)
        if date_to is not None:
            conditions.append("DATE(created_at) <= ?")
            params.append(date_to)
        if key_id is not None:
            conditions.append("virtual_key_id = ?")
            params.append(key_id)
        if model is not None:
            conditions.append("model = ?")
            params.append(model)

        where = "WHERE " + " AND ".join(conditions)
        sql = f"""
            SELECT
                strftime('%Y-%m-%d %H:00', created_at) AS hour,
                COUNT(*) AS request_count,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cost), 0) AS cost
            FROM requests
            {where}
            GROUP BY hour
            ORDER BY hour ASC
        """
        async with self.db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return _rows_to_dicts(rows)

    async def get_model_stats(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate daily_usage by model."""
        conditions: list[str] = []
        params: list[Any] = []

        if date_from is not None:
            conditions.append("date >= ?")
            params.append(date_from)
        if date_to is not None:
            conditions.append("date <= ?")
            params.append(date_to)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        sql = f"""
            SELECT model,
                   SUM(request_count)  AS request_count,
                   SUM(input_tokens)   AS input_tokens,
                   SUM(output_tokens)  AS output_tokens,
                   SUM(cost)           AS cost
            FROM daily_usage
            {where}
            GROUP BY model
            ORDER BY cost DESC
        """
        async with self.db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return _rows_to_dicts(rows)

    async def get_provider_stats(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate daily_usage by provider."""
        conditions: list[str] = []
        params: list[Any] = []

        if date_from is not None:
            conditions.append("date >= ?")
            params.append(date_from)
        if date_to is not None:
            conditions.append("date <= ?")
            params.append(date_to)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        sql = f"""
            SELECT provider,
                   SUM(request_count)  AS request_count,
                   SUM(input_tokens)   AS input_tokens,
                   SUM(output_tokens)  AS output_tokens,
                   SUM(cost)           AS cost
            FROM daily_usage
            {where}
            GROUP BY provider
            ORDER BY cost DESC
        """
        async with self.db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return _rows_to_dicts(rows)

    async def get_latency_percentiles(
        self,
        date_from: str | None = None,
        model: str | None = None,
    ) -> dict[str, float | None]:
        """Calculate latency percentiles (p50, p90, p95, p99) from recent requests.
        
        Returns dict with p50, p90, p95, p99 latency in ms.
        """
        conditions: list[str] = ["status = 'success'"]
        params: list[Any] = []

        if date_from is not None:
            conditions.append("created_at >= ?")
            params.append(date_from)
        if model is not None:
            conditions.append("model = ?")
            params.append(model)

        where = " AND ".join(conditions)

        # Get all latencies
        sql = f"SELECT latency_ms FROM requests WHERE {where} AND latency_ms IS NOT NULL ORDER BY latency_ms"
        async with self.db.execute(sql, params) as cur:
            rows = await cur.fetchall()

        latencies = [r[0] for r in rows if r[0] is not None]

        if not latencies:
            return {"p50": None, "p90": None, "p95": None, "p99": None}

        latencies.sort()
        n = len(latencies)

        def percentile(p: int) -> float:
            idx = int(n * p / 100)
            idx = min(idx, n - 1)
            return latencies[idx]

        return {
            "p50": round(percentile(50), 2),
            "p90": round(percentile(90), 2),
            "p95": round(percentile(95), 2),
            "p99": round(percentile(99), 2),
        }

    async def get_error_stats(
        self,
        date_from: str | None = None,
    ) -> dict[str, Any]:
        """Get error statistics from recent requests.
        
        Returns:
            - total_requests: total request count
            - successful_requests: successful request count
            - failed_requests: failed request count
            - error_rate: percentage of failed requests
            - errors_by_type: breakdown of error types
        """
        conditions: list[str] = []
        params: list[Any] = []

        if date_from is not None:
            conditions.append("created_at >= ?")
            params.append(date_from)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        # Get totals
        async with self.db.execute(
            f"SELECT COUNT(*), SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) FROM requests {where}",
            params
        ) as cur:
            row = await cur.fetchone()
            total = row[0]
            successful = row[1] or 0
            failed = row[2] or 0

        error_rate = (failed / total * 100) if total > 0 else 0.0

        # Get error breakdown
        async with self.db.execute(
            f"""
            SELECT 
                CASE 
                    WHEN error_message LIKE '%401%' THEN 'Authentication'
                    WHEN error_message LIKE '%404%' THEN 'Not Found'
                    WHEN error_message LIKE '%429%' THEN 'Rate Limit'
                    WHEN error_message LIKE '%500%' OR error_message LIKE '%502%' OR error_message LIKE '%503%' THEN 'Server Error'
                    WHEN error_message LIKE '%timeout%' OR error_message LIKE '%Timeout%' THEN 'Timeout'
                    ELSE 'Other'
                END as error_type,
                COUNT(*) as count
            FROM requests 
            {where}
            AND status = 'error'
            GROUP BY error_type
            ORDER BY count DESC
            """,
            params
        ) as cur:
            error_rows = await cur.fetchall()

        errors_by_type = {r[0]: r[1] for r in error_rows}

        return {
            "total_requests": total,
            "successful_requests": successful,
            "failed_requests": failed,
            "error_rate": round(error_rate, 2),
            "errors_by_type": errors_by_type,
        }

    async def get_summary(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        """Return high-level totals: request count, tokens, cost, unique models."""
        conditions: list[str] = []
        params: list[Any] = []

        if date_from is not None:
            conditions.append("date >= ?")
            params.append(date_from)
        if date_to is not None:
            conditions.append("date <= ?")
            params.append(date_to)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        sql = f"""
            SELECT COALESCE(SUM(request_count), 0)  AS total_requests,
                   COALESCE(SUM(input_tokens),  0)   AS total_input_tokens,
                   COALESCE(SUM(output_tokens), 0)   AS total_output_tokens,
                   COALESCE(SUM(cost), 0.0)           AS total_cost,
                   COUNT(DISTINCT model)              AS unique_models
            FROM daily_usage
            {where}
        """
        async with self.db.execute(sql, params) as cur:
            row = await cur.fetchone()
        return _row_to_dict(row)  # type: ignore[return-value]

    # -- token limits --------------------------------------------------------

    async def check_token_limit(self, key_id: int) -> bool:
        """Return True if the key is under its token_limit.

        Keys with token_limit=None are always considered under limit.
        Keys without a matching row also return True.
        """
        async with self.db.execute(
            "SELECT token_limit, tokens_used FROM virtual_keys WHERE id = ?",
            (key_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return True
        limit: int | None = row["token_limit"]
        if limit is None:
            return True
        return row["tokens_used"] < limit

    # -- config overrides ----------------------------------------------------

    async def get_config_override(self, key: str) -> str | None:
        """Get a single config override value by key."""
        async with self.db.execute(
            "SELECT value FROM config_overrides WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return row["value"] if row else None

    async def get_all_config_overrides(self) -> dict[str, str]:
        """Return all config overrides as {key: value} dict."""
        async with self.db.execute(
            "SELECT key, value FROM config_overrides ORDER BY key"
        ) as cur:
            rows = await cur.fetchall()
        return {row["key"]: row["value"] for row in rows}

    async def set_config_override(self, key: str, value: str) -> None:
        """Upsert a config override."""
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """
            INSERT INTO config_overrides (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
        await self.db.commit()

    async def delete_config_override(self, key: str) -> bool:
        """Delete a config override. Returns True if a row was deleted."""
        cursor = await self.db.execute(
            "DELETE FROM config_overrides WHERE key = ?", (key,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def set_config_overrides_bulk(self, overrides: dict[str, str]) -> None:
        """Set multiple config overrides in a single transaction."""
        now = datetime.now(timezone.utc).isoformat()
        for key, value in overrides.items():
            await self.db.execute(
                """
                INSERT INTO config_overrides (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
        await self.db.commit()

    # -- prometheus metrics ---------------------------------------------------

    async def get_prometheus_metrics(self) -> str:
        """Return Prometheus text-format metrics from daily_usage + recent requests.

        Counters (from daily_usage, cumulative):
          - llm_gateway_requests_total
          - llm_gateway_input_tokens_total
          - llm_gateway_output_tokens_total
          - llm_gateway_cost_total

        Gauges (from last N requests):
          - llm_gateway_latency_ms_avg
          - llm_gateway_ttft_ms_avg
          - llm_gateway_tps_avg
        """
        lines: list[str] = []

        # --- Counters from daily_usage ---
        # Overall totals
        async with self.db.execute(
            "SELECT COALESCE(SUM(request_count),0),"
            "       COALESCE(SUM(input_tokens),0),"
            "       COALESCE(SUM(output_tokens),0),"
            "       COALESCE(SUM(cost),0.0)"
            " FROM daily_usage"
        ) as cur:
            row = await cur.fetchone()
            total_requests = row[0]
            total_input = row[1]
            total_output = row[2]
            total_cost = row[3]

        # Per-model breakdown
        async with self.db.execute(
            "SELECT model, SUM(request_count), SUM(input_tokens),"
            "       SUM(output_tokens), SUM(cost)"
            " FROM daily_usage GROUP BY model ORDER BY SUM(cost) DESC"
        ) as cur:
            model_rows = await cur.fetchall()

        # Per-provider breakdown
        async with self.db.execute(
            "SELECT provider, SUM(request_count), SUM(input_tokens),"
            "       SUM(output_tokens), SUM(cost)"
            " FROM daily_usage GROUP BY provider ORDER BY SUM(cost) DESC"
        ) as cur:
            provider_rows = await cur.fetchall()

        # Per-model+provider breakdown
        async with self.db.execute(
            "SELECT model, provider, SUM(request_count), SUM(input_tokens),"
            "       SUM(output_tokens), SUM(cost)"
            " FROM daily_usage GROUP BY model, provider ORDER BY SUM(cost) DESC"
        ) as cur:
            mp_rows = await cur.fetchall()

        def _esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

        # Requests total
        lines.append("# HELP llm_gateway_requests_total Total number of proxied requests.")
        lines.append("# TYPE llm_gateway_requests_total counter")
        lines.append(f"llm_gateway_requests_total {total_requests}")
        for r in model_rows:
            lines.append(f'llm_gateway_requests_total{{model="{_esc(r[0])}"}} {r[1]}')
        for r in provider_rows:
            lines.append(f'llm_gateway_requests_total{{provider="{_esc(r[0])}"}} {r[1]}')
        for r in mp_rows:
            lines.append(f'llm_gateway_requests_total{{model="{_esc(r[0])}",provider="{_esc(r[1])}"}} {r[2]}')

        # Input tokens total
        lines.append("")
        lines.append("# HELP llm_gateway_input_tokens_total Total input tokens processed.")
        lines.append("# TYPE llm_gateway_input_tokens_total counter")
        lines.append(f"llm_gateway_input_tokens_total {total_input}")
        for r in model_rows:
            lines.append(f'llm_gateway_input_tokens_total{{model="{_esc(r[0])}"}} {r[2]}')
        for r in provider_rows:
            lines.append(f'llm_gateway_input_tokens_total{{provider="{_esc(r[0])}"}} {r[2]}')

        # Output tokens total
        lines.append("")
        lines.append("# HELP llm_gateway_output_tokens_total Total output tokens generated.")
        lines.append("# TYPE llm_gateway_output_tokens_total counter")
        lines.append(f"llm_gateway_output_tokens_total {total_output}")
        for r in model_rows:
            lines.append(f'llm_gateway_output_tokens_total{{model="{_esc(r[0])}"}} {r[3]}')
        for r in provider_rows:
            lines.append(f'llm_gateway_output_tokens_total{{provider="{_esc(r[0])}"}} {r[3]}')

        # Cost total
        lines.append("")
        lines.append("# HELP llm_gateway_cost_total Total cost in USD.")
        lines.append("# TYPE llm_gateway_cost_total counter")
        lines.append(f"llm_gateway_cost_total {total_cost:.6f}")
        for r in model_rows:
            lines.append(f'llm_gateway_cost_total{{model="{_esc(r[0])}"}} {r[4]:.6f}')
        for r in provider_rows:
            lines.append(f'llm_gateway_cost_total{{provider="{_esc(r[0])}"}} {r[4]:.6f}')

        # --- Gauges from recent requests (last 1/5/15 per model) ---
        # Get recent requests for latency/TPS calculations
        async with self.db.execute(
            "SELECT model, latency_ms, time_to_first_token_ms, tokens_per_second"
            " FROM requests WHERE status='success' ORDER BY id DESC LIMIT 500"
        ) as cur:
            recent = await cur.fetchall()

        # Group by model
        from collections import defaultdict
        by_model: dict[str, list[tuple]] = defaultdict(list)
        all_latencies = []
        all_ttft = []
        all_tps = []
        for r in recent:
            model, lat, ttft, tps = r[0], r[1], r[2], r[3]
            by_model[model].append((lat, ttft, tps))
            if lat is not None:
                all_latencies.append(lat)
            if ttft is not None:
                all_ttft.append(ttft)
            if tps is not None:
                all_tps.append(tps)

        def _avg(vals: list, n: int) -> float:
            return sum(vals[:n]) / n if vals[:n] else 0.0

        for window in (1, 5, 15):
            lines.append("")
            lines.append(
                f"# HELP llm_gateway_latency_ms_last_{window} "
                f"Average latency in ms over last {window} successful requests."
            )
            lines.append(f"# TYPE llm_gateway_latency_ms_last_{window} gauge")
            lines.append(f"llm_gateway_latency_ms_last_{window} {_avg(all_latencies, window):.2f}")
            for model, entries in sorted(by_model.items()):
                lats = [e[0] for e in entries if e[0] is not None]
                lines.append(f'llm_gateway_latency_ms_last_{window}{{model="{_esc(model)}"}} {_avg(lats, window):.2f}')

            lines.append("")
            lines.append(
                f"# HELP llm_gateway_ttft_ms_last_{window} "
                f"Average time-to-first-token in ms over last {window} successful requests."
            )
            lines.append(f"# TYPE llm_gateway_ttft_ms_last_{window} gauge")
            lines.append(f"llm_gateway_ttft_ms_last_{window} {_avg(all_ttft, window):.2f}")
            for model, entries in sorted(by_model.items()):
                ttfts = [e[1] for e in entries if e[1] is not None]
                lines.append(f'llm_gateway_ttft_ms_last_{window}{{model="{_esc(model)}"}} {_avg(ttfts, window):.2f}')

            lines.append("")
            lines.append(
                f"# HELP llm_gateway_tps_last_{window} "
                f"Average tokens per second over last {window} successful requests."
            )
            lines.append(f"# TYPE llm_gateway_tps_last_{window} gauge")
            lines.append(f"llm_gateway_tps_last_{window} {_avg(all_tps, window):.2f}")
            for model, entries in sorted(by_model.items()):
                tpss = [e[2] for e in entries if e[2] is not None]
                lines.append(f'llm_gateway_tps_last_{window}{{model="{_esc(model)}"}} {_avg(tpss, window):.2f}')

        lines.append("")
        return "\n".join(lines)

    # -- maintenance ---------------------------------------------------------

    async def prune_bodies(self, older_than_days: int = 30) -> int:
        """Set request_body and response_body to NULL for rows older than N days.

        Returns the number of rows affected.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=older_than_days)
        ).isoformat()
        cursor = await self.db.execute(
            """
            UPDATE requests
            SET request_body = NULL, response_body = NULL
            WHERE created_at < ?
              AND (request_body IS NOT NULL OR response_body IS NOT NULL)
            """,
            (cutoff,),
        )
        await self.db.commit()
        return cursor.rowcount

    async def purge_old_data(self, retention_days: int = 7) -> dict[str, int]:
        """Delete all data older than retention_days.

        Removes:
        - requests older than retention_days
        - daily_usage entries older than retention_days
        - model_stats entries older than retention_days

        Returns a dict with counts of deleted records.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()

        deleted = {"requests": 0, "daily_usage": 0, "model_stats": 0}

        # Delete old requests
        async with self.db.execute(
            "DELETE FROM requests WHERE created_at < ?", (cutoff,)
        ) as cursor:
            deleted["requests"] = cursor.rowcount

        # Delete old daily_usage
        async with self.db.execute(
            "DELETE FROM daily_usage WHERE date < ?", (cutoff[:10],)
        ) as cursor:
            deleted["daily_usage"] = cursor.rowcount

        # Delete old model_stats (if table exists)
        try:
            async with self.db.execute(
                "DELETE FROM model_stats WHERE date < ?", (cutoff[:10],)
            ) as cursor:
                deleted["model_stats"] = cursor.rowcount
        except sqlite3.OperationalError:
            # model_stats table may not exist in older schemas
            deleted["model_stats"] = 0

        await self.db.commit()
        return deleted
