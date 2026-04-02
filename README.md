# LLM Gateway

[![CI](https://github.com/rohitpaul/llm-gateway/workflows/CI/badge.svg)](https://github.com/rohitpaul/llm-gateway/actions/workflows/ci.yml)
[![Security](https://github.com/rohitpaul/llm-gateway/workflows/Security%20Audit/badge.svg)](https://github.com/rohitpaul/llm-gateway/actions/workflows/security-audit.yml)
[![Release](https://github.com/rohitpaul/llm-gateway/workflows/Release/badge.svg)](https://github.com/rohitpaul/llm-gateway/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Lightweight LLM proxy with virtual API keys, per-model usage tracking, and a clean web dashboard. Drop-in LiteLLM replacement.

## Features

- **Virtual API Keys** — Create scoped keys with provider/model filters and token limits
- **Per-Model Usage** — Track cost, tokens, and latency per model and provider
- **OpenAI-Compatible** — Drop-in replacement for `/v1/chat/completions` and `/v1/models`
- **Multi-Provider** — OpenAI, Anthropic, Gemini, DeepSeek, Groq, Mistral, xAI, OpenRouter, Together, Fireworks, Perplexity
- **Streaming** — Full SSE streaming support
- **Web Dashboard** — Stats, request history, key management UI
- **SQLite** — Zero external deps, no Postgres needed
- **Single Container** — Docker deploy in seconds

## Quick Start

### Docker (recommended)

```bash
# Clone
git clone https://github.com/rohitpaul/llm-gateway.git && cd llm-gateway

# Configure
cp .env.example .env
# Edit .env with your API keys and set GATEWAY_ADMIN_KEY

# Run
docker compose up -d

# Open dashboard
open http://localhost:4000
```

### Local

```bash
pip install fastapi uvicorn[standard] httpx aiosqlite pyyaml python-dotenv pydantic
cp .env.example .env  # add your keys
python -m app.server
```

## Usage

### Create a Virtual Key

```bash
curl -X POST http://localhost:4000/admin/keys \
  -H "Authorization: Bearer sk-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-app", "provider_filter": "openai,anthropic", "token_limit": 1000000}'
```

Response:
```json
{
  "id": 1,
  "name": "my-app",
  "key": "sk-gw-abc123...",
  "provider_filter": "openai,anthropic",
  "token_limit": 1000000
}
```

### Use as OpenAI Client

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-gw-abc123...",  # your virtual key
    base_url="http://localhost:4000/v1"
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

### Use with Claude Code / Cursor / Continue.dev

Point the tool at `http://localhost:4000/v1` and use your virtual key as the API key.

## API Endpoints

### Proxy (OpenAI-compatible)
| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completions (streaming supported) |
| `/chat/completions` | POST | Alternate path |
| `/v1/models` | GET | List available models |
| `/health` | GET | Health check |

### Admin
| Endpoint | Method | Description |
|---|---|---|
| `/admin/keys` | POST | Create virtual key |
| `/admin/keys` | GET | List all keys |
| `/admin/keys/{id}/deactivate` | POST | Deactivate a key |
| `/admin/keys/{id}` | DELETE | Delete a key |

### Stats
| Endpoint | Method | Description |
|---|---|---|
| `/api/stats/summary` | GET | Total requests, cost, tokens |
| `/api/stats/models` | GET | Breakdown by model |
| `/api/stats/providers` | GET | Breakdown by provider |
| `/api/stats/daily` | GET | Daily aggregated stats |
| `/api/requests` | GET | Paginated request history |

## Configuration

### config.yaml

```yaml
# Model routing overrides (optional — auto-detected if not set)
models:
  gpt-4o:
    provider: openai

# Provider overrides (optional)
providers:
  openai:
    base_url: https://api.openai.com/v1
```

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key | |
| `ANTHROPIC_API_KEY` | Anthropic API key | |
| `GEMINI_API_KEY` | Google Gemini API key | |
| `OPENROUTER_API_KEY` | OpenRouter API key | |
| `DEEPSEEK_API_KEY` | DeepSeek API key | |
| `GROQ_API_KEY` | Groq API key | |
| `MISTRAL_API_KEY` | Mistral API key | |
| `GATEWAY_ADMIN_KEY` | Admin key for dashboard/management | *(required — server exits if unset)* |
| `GATEWAY_DB` | SQLite database path | `data/gateway.db` |
| `GATEWAY_PORT` | Server port | `4000` |
| `GATEWAY_CORS_ORIGINS` | Comma-separated allowed CORS origins | *(empty = no CORS)* |

## Reverse Proxy Configuration

For production deployments, use a reverse proxy with these critical settings:

### Option 1: Manual Nginx Setup
See [REVERSE_PROXY.md](REVERSE_PROXY.md) for comprehensive guide.

**Critical settings for LLM workloads:**
```nginx
proxy_buffering off;     # Required for SSE streaming
proxy_read_timeout 300s;  # LLM requests can be slow
client_max_body_size 10M; # Large context windows
```

### Option 2: Nginx Proxy Manager (Web UI)
See [REVERSE_PROXY_NPM.md](REVERSE_PROXY_NPM.md) for easy setup with automatic SSL.

**Quick start:**
```bash
docker compose -f examples/docker-compose-nginx.yml up -d
# UI at http://localhost:81 (admin@example.com / changeme)
```

### Other Options
- **Traefik** - Docker-native with auto-discovery
- **Caddy** - Automatic HTTPS with simple config
- **HAProxy** - Advanced load balancing

See [REVERSE_PROXY.md](REVERSE_PROXY.md) for all configurations.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## CI/CD Pipeline

This project uses a comprehensive CI/CD pipeline:

- **Automated Testing**: Linting, type checking, unit tests, E2E tests
- **Docker Builds**: Multi-architecture images (amd64, arm64) published to GHCR
- **Security Scanning**: Weekly vulnerability audits with Trivy
- **Automated Releases**: GitHub releases with auto-generated changelogs
- **Dependabot**: Weekly dependency updates

See [.github/CI_CD_GUIDE.md](.github/CI_CD_GUIDE.md) for details.

## License

MIT
