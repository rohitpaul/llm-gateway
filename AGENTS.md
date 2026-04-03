# AGENTS.md - Coding Agent Guidelines

Guidelines for AI coding agents working in the LLM Gateway codebase.

## Key Directives

1. **Never commit secrets** — Use env vars, never hardcode API keys
2. **Always type hints** — Use Python 3.10+ union syntax (`int | None`)
3. **Async-first** — All I/O must be async, use `async with` for clients
4. **Error handling** — Use `HTTPException` for HTTP errors, catch specific exceptions
5. **Tests** — Place in `tests/`, name `test_<module>.py`, use `pytest-asyncio`
6. **Lint before commit** — Run `ruff format && ruff check && mypy`

## Project Overview

Lightweight LLM proxy with virtual API keys, multi-provider support, and usage tracking.

**Tech Stack**: Python 3.11+, FastAPI, SQLite (aiosqlite), httpx, Alpine.js, Tailwind CSS

## Build/Lint/Test Commands

### Running the Server

```bash
python -m app.server              # Local development
docker compose up -d              # Docker
docker compose logs -f llm-gateway
```

### Dependencies

```bash
pip install -e .
# pip install fastapi[standard] uvicorn[standard] httpx aiosqlite pyyaml python-dotenv pydantic
```

### Linting and Formatting

```bash
pip install ruff mypy
ruff format app/ && ruff check app/ && mypy app/ --ignore-missing-imports
```

### Testing

```bash
pip install pytest pytest-asyncio pytest-cov
pytest                                    # Run all tests
pytest tests/test_database.py::test_name -v  # Run single test
pytest --cov=app --cov-report=html        # Run with coverage
```

## Code Style Guidelines

### Imports

Group: future annotations → stdlib → third-party → local. Always use `from __future__ import annotations`.

```python
from __future__ import annotations
import hashlib
from typing import Any
import httpx
from fastapi import FastAPI, Request, HTTPException
from app import config
from app.database import Database
```

### Type Hints

Use Python 3.10+ union syntax. Type parameters and returns.

```python
def create_key(self, name: str, key_hash: str, token_limit: int | None = None) -> int:
    ...
```

### Naming Conventions

- Functions/Variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private: prefix with `_`

### Error Handling

Use `HTTPException` for HTTP errors. Log with context. Catch specific exceptions.

```python
if not key_info:
    raise HTTPException(status_code=401, detail="Invalid API key")

try:
    response = await proxy_chat_completions(body, provider, config)
except httpx.HTTPStatusError as e:
    logger.error("Upstream HTTP %d error: %s", e.response.status_code, str(e)[:200])
    raise HTTPException(status_code=502, detail=f"Upstream error: {str(e)[:200]}")
```

### Async/Await

All I/O must be async. Use `async with` for context managers.

```python
async with httpx.AsyncClient(timeout=timeout) as client:
    response = await client.post(url, headers=headers, json=body)
```

### Documentation

Use triple-quoted docstrings. First line is concise summary.

```python
def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for a request."""
    ...
```

### Code Organization

Group related functions with comment headers. Keep functions < 50 lines.

```python
# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------
async def verify_virtual_key(request: Request) -> dict:
    ...
```

### Database Patterns

Use `Database` class methods. Always async. Handle `None` returns.

```python
key_info = await db.validate_key(key_hash)
if not key_info:
    raise HTTPException(status_code=401, detail="Invalid API key")
```

### Logging

Use structured logging with context.

```python
logger = logging.getLogger("llm-gateway")
logger.info("Request completed: model=%s provider=%s latency=%.2fms", model, provider, latency)
logger.error("Database error: %s", str(e))
logger.exception("Unexpected error")
```

### Testing Conventions

Place tests in `tests/`. Name files `test_<module>.py`. Use `pytest-asyncio`.

```python
@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()

@pytest.mark.asyncio
async def test_create_key(db):
    key_id = await db.create_key(name="test-key", key_hash="abc123", key_prefix="sk-test")
    assert key_id > 0
```

## Common Patterns

### Adding a New Provider

1. Add to `PROVIDER_BASE_URLS` in `app/config.py`
2. Add env var mapping in `get_provider_api_key()`
3. Add header logic in `_build_headers()` in `app/providers/__init__.py`
4. Add pricing to `PRICING` dict

### Adding a New Endpoint

1. Add route handler in `app/server.py`
2. Use auth decorator (`verify_virtual_key` or `verify_admin`)
3. Add docstring
4. Update README.md

### Modifying Database Schema

1. Increment `SCHEMA_VERSION` in `app/database.py`
2. Add migration to `_MIGRATIONS` dict
3. Test with fresh and existing DB

## Project Notes

- SQLite uses WAL mode for concurrency
- Streaming uses transparent pass-through
- Virtual keys hashed with SHA-256
- Request/response bodies truncated at 100KB
- Default retention: 7 days
- Admin key mandatory (server exits if unset)

## Cursor Rules & Copilot Instructions

No Cursor rules (`.cursor/rules/` or `.cursorrules`) or Copilot rules (`.github/copilot-instructions.md`) exist in this repository.
