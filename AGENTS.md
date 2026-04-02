# AGENTS.md - Coding Agent Guidelines

Guidelines for AI coding agents working in the LLM Gateway codebase.

## Project Overview

Lightweight LLM proxy with virtual API keys, multi-provider support, and usage tracking.

**Tech Stack**: Python 3.11+, FastAPI, SQLite (aiosqlite), httpx, Alpine.js, Tailwind CSS

## Build/Lint/Test Commands

### Running the Server

```bash
# Local development
python -m app.server

# Docker
docker compose up -d
docker compose logs -f llm-gateway
```

### Dependencies

```bash
pip install -e .
# Or: pip install fastapi[standard] uvicorn[standard] httpx aiosqlite pyyaml python-dotenv pydantic
```

### Linting and Formatting

```bash
pip install ruff mypy
ruff format app/ && ruff check app/ && mypy app/ --ignore-missing-imports
```

### Testing

No test suite yet. When adding tests:

```bash
pip install pytest pytest-asyncio pytest-cov
pytest                                    # Run all tests
pytest tests/test_database.py::test_name -v  # Run single test
pytest --cov=app --cov-report=html        # Run with coverage
```

## Code Style Guidelines

### Imports

Group in order: future annotations → stdlib → third-party → local. Always use `from __future__ import annotations` first.

```python
from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx
from fastapi import FastAPI, Request, HTTPException

from app import config
from app.database import Database
```

- Use absolute imports for local modules (`from app import config`)
- Import specific names (`from fastapi import HTTPException`)

### Type Hints

Use Python 3.10+ union syntax. Always type parameters and return values.

```python
def create_key(self, name: str, key_hash: str, token_limit: int | None = None) -> int:
    ...

async def get_stats(self, model: str | None = None) -> dict[str, Any]:
    ...
```

### Naming Conventions

- Functions/Variables: `snake_case` (`create_key`, `key_hash`)
- Classes: `PascalCase` (`Database`, `VirtualKey`)
- Constants: `UPPER_SNAKE_CASE` (`MAX_BODY_SIZE`)
- Private functions: prefix with `_` (`_build_headers`)

### Error Handling

Use `HTTPException` for HTTP errors. Log errors with context. Catch specific exceptions.

```python
async def verify_key(key_hash: str) -> dict[str, Any]:
    key_info = await db.validate_key(key_hash)
    if not key_info:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return key_info

try:
    response = await proxy_chat_completions(body, provider, config)
except httpx.HTTPStatusError as e:
    logger.error("Upstream HTTP %d error: provider=%s model=%s — %s",
                 e.response.status_code, provider, model, str(e)[:200])
    raise HTTPException(status_code=502, detail=f"Upstream error: {str(e)[:200]}")
```

### Async/Await

All I/O operations must be async. Use `async with` for context managers.

```python
async with httpx.AsyncClient(timeout=timeout) as client:
    response = await client.post(url, headers=headers, json=body)

async for chunk in response.aiter_bytes():
    yield chunk
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

Use `Database` class methods. Always use async. Handle `None` returns.

```python
key_info = await db.validate_key(key_hash)
if not key_info:
    raise HTTPException(status_code=401, detail="Invalid API key")
```

### FastAPI Patterns

Use dependency injection for auth. Return dicts or Response objects.

```python
@app.get("/api/stats/summary")
async def get_summary(_: dict = Depends(verify_admin)) -> dict:
    return await db.get_summary_stats()
```

### Configuration

- Environment variables → `.env` file
- API keys → env vars in `config.py`
- Model/provider routing → `config.yaml`
- Never hardcode secrets

### Logging

Use structured logging with context.

```python
logger = logging.getLogger("llm-gateway")
logger.info("Request completed: model=%s provider=%s latency=%.2fms", model, provider, latency)
logger.error("Database error: %s", str(e))
logger.exception("Unexpected error")  # Includes stack trace
```

### Testing Conventions

Place tests in `tests/`. Name files `test_<module>.py`. Use `pytest-asyncio` for async.

```python
# tests/test_database.py
import pytest
from app.database import Database

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
2. Add env var mapping in `get_provider_api_key()` in `app/config.py`
3. Add header logic in `_build_headers()` in `app/providers/__init__.py`
4. Add pricing to `PRICING` dict in `app/providers/__init__.py`

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
