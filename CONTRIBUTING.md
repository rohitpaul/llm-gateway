# Development Guide

## Prerequisites

- Python 3.11+
- Docker (optional, for containerized deployment)
- Git

## Local Setup

```bash
# Clone repository
git clone https://github.com/rohitpaul/llm-gateway.git
cd llm-gateway

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .

# Install dev dependencies
pip install ruff mypy pytest pytest-asyncio pytest-cov

# Create config
cp .env.example .env
cp config.yaml.example config.yaml

# Edit .env and set your API keys and GATEWAY_ADMIN_KEY
```

## Running the Server

```bash
# Development
python -m app.server

# With custom port
GATEWAY_PORT=8080 python -m app.server

# Docker
docker compose up -d
```

## Code Quality

```bash
# Format code
ruff format app/

# Lint code
ruff check app/

# Type check
mypy app/ --ignore-missing-imports

# Run all checks
ruff format app/ && ruff check app/ && mypy app/ --ignore-missing-imports
```

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test
pytest tests/test_database.py::test_create_key -v
```

## Making Changes

1. **Create a branch**:
   ```bash
   git checkout -b feat/my-feature
   ```

2. **Make commits** (follow conventional commits):
   ```bash
   git commit -m "feat: add new feature"
   ```

3. **Push and create PR**:
   ```bash
   git push -u origin feat/my-feature
   gh pr create
   ```

4. **Ensure CI passes** before requesting review

## Project Structure

```
.
├── app/
│   ├── __init__.py
│   ├── server.py          # FastAPI app and routes
│   ├── database.py        # SQLite database layer
│   ├── config.py          # Configuration loading
│   └── providers/
│       └── __init__.py    # Provider adapters
├── static/                 # Frontend assets
├── templates/              # Jinja2 templates
├── tests/                  # Test suite (to be added)
├── .github/
│   ├── workflows/         # GitHub Actions workflows
│   ├── ISSUE_TEMPLATE/    # Issue templates
│   └── PULL_REQUEST_TEMPLATE.md
├── AGENTS.md              # Guidelines for AI coding agents
├── README.md              # Project documentation
├── config.yaml.example    # Example configuration
├── .env.example           # Example environment variables
├── pyproject.toml         # Python project metadata
├── Dockerfile             # Docker image definition
└── docker-compose.yml     # Docker Compose setup
```

## Database Migrations

When modifying the database schema:

1. Increment `SCHEMA_VERSION` in `app/database.py`
2. Add migration to `_MIGRATIONS` dict
3. Test with fresh and existing databases

## Adding a New Provider

1. Add to `PROVIDER_BASE_URLS` in `app/config.py`
2. Add env var mapping in `get_provider_api_key()`
3. Add header logic in `_build_headers()` in `app/providers/__init__.py`
4. Add pricing to `PRICING` dict

## Debugging

```bash
# View server logs
docker compose logs -f llm-gateway

# Check database
sqlite3 data/gateway.db
> .tables
> SELECT * FROM virtual_keys;
> SELECT * FROM requests ORDER BY created_at DESC LIMIT 10;
```

## Useful Commands

```bash
# Create a virtual key
curl -X POST http://localhost:4000/admin/keys \
  -H "Authorization: Bearer sk-admin-xxx" \
  -H "Content-Type: application/json" \
  -d '{"name": "test-key"}'

# Test health endpoint
curl http://localhost:4000/health

# View stats
curl -H "Authorization: Bearer sk-admin-xxx" \
  http://localhost:4000/api/stats/summary
```

## Release Process

See [CI/CD Guide](/.github/CI_CD_GUIDE.md) for release process.
