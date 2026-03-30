# LLM Gateway — Security Remediation Plan

Created: March 30, 2026
Status: In Progress

## Immediate Actions (Priority Order)

### 1. Remove Default Admin Key
**Severity:** CRITICAL
**Files:** `app/config.py`, `docker-compose.yml`

- [ ] `app/config.py:15` — Remove the `"sk-admin-change-me"` default fallback. If env var is not set, print a clear error and exit.
- [ ] `docker-compose.yml:16` — Remove the default fallback `${GATEWAY_ADMIN_KEY:-sk-admin-change-me}`. Just use `${GATEWAY_ADMIN_KEY}` and fail if missing.
- [ ] Verify the existing `.env` has a strong random admin key set. If not, generate one with `python3 -c "print('sk-adm-' + __import__('secrets').token_hex(24))"`.
- [ ] Test: container should fail to start without GATEWAY_ADMIN_KEY set.
- [ ] Commit.

### 2. Add Auth to /metrics Endpoint
**Severity:** CRITICAL
**Files:** `app/server.py`

- [ ] Add `verify_admin` dependency to the `/metrics` route handler.
- [ ] If Prometheus needs unauthenticated access, add a config flag `GATEWAY_METRICS_NO_AUTH=true` (default false) with a startup warning when enabled.
- [ ] Update Grafana datasource or Prometheus scrape config if needed (Prometheus would need to send the admin key as a Bearer token, or use the flag).
- [ ] Test: `curl /metrics` without auth returns 401.
- [ ] Commit.

### 3. Add Auth to /v1/models Endpoint
**Severity:** CRITICAL
**Files:** `app/server.py`

- [ ] Add `verify_virtual_key` dependency to both `/v1/models` and `/models` routes.
- [ ] This means clients calling `/v1/models` need to send their virtual key or admin key — standard OpenAI behavior.
- [ ] Test: `curl /v1/models` without auth returns 401. With valid key returns model list.
- [ ] Commit.

### 4. Add Auth to /api/health/providers Endpoint
**Severity:** CRITICAL
**Files:** `app/server.py`

- [ ] Add `verify_admin` dependency to the `/api/health/providers` route handler.
- [ ] Test: `curl /api/health/providers` without auth returns 401.
- [ ] Commit.

### 5. Restrict CORS Policy
**Severity:** HIGH
**Files:** `app/server.py`

- [ ] Add `GATEWAY_CORS_ORIGINS` env var (comma-separated list of allowed origins).
- [ ] Default to `""` (no CORS) if not set. In docker-compose, set it to the dashboard URL.
- [ ] If the value is `"*"` (explicit opt-in), allow the current wildcard behavior but log a warning at startup.
- [ ] Replace `allow_origins=["*"]` on line 243 with the parsed list.
- [ ] Test: cross-origin request from random domain is rejected.
- [ ] Commit.

## Implementation Notes

- Each item is a single commit with a clear message like `fix(security): add auth to /metrics endpoint`.
- Run the E2E test workflow after all changes to verify nothing is broken.
- The E2E test uses `GATEWAY_ADMIN_KEY` env var so items 2-4 should pass once that var is set.
- After all 5 items, do a full redeploy on Unraid via Dockhand.

## Post-Immediate (Next Sprint)

These are tracked here for continuity but NOT part of this iteration:
- Rate limiting (slowapi middleware)
- Gemini API key in header instead of URL
- Request body size limit
- Constant-time admin key comparison (`hmac.compare_digest`)
- CSRF protection / httpOnly cookies for dashboard auth
- TLS termination
- API key encryption at rest in SQLite
- Dependency pinning + SRI hashes for CDN scripts
