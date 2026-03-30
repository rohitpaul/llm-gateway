"""FastAPI server — LLM Gateway proxy with virtual key auth and dashboard."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import argparse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.cors import CORSMiddleware
import httpx

from app import config
from app.database import Database
from app.providers import proxy_chat_completions, resolve_provider, calculate_cost, PRICING, _infer_provider, _get_api_key

# Max body size to store in the DB (bytes) — larger bodies are truncated
_MAX_BODY_SIZE = 100_000  # 100 KB

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

db = Database()
app_config: dict = {}
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "templates"))



def _int_param(request: Request, name: str, default: int | None = None) -> int | None:
    """Parse an integer query parameter from a Starlette Request."""
    val = request.query_params.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _serialize_body(body: Any, max_bytes: int = _MAX_BODY_SIZE) -> str | None:
    """Serialize a request/response body to JSON string for DB storage.

    Returns None if the body is empty. Truncates if serialized form
    exceeds max_bytes.
    """
    if body is None:
        return None
    try:
        text = json.dumps(body, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(body)
    if len(text) > max_bytes:
        text = text[:max_bytes] + "\n... [truncated]"
    return text

# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def hash_key(key: str) -> str:
    """SHA-256 hash of a virtual key."""
    return hashlib.sha256(key.encode()).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Generate a new virtual key. Returns (plaintext_key, key_hash, key_prefix)."""
    raw = f"sk-gw-{secrets.token_hex(20)}"
    return raw, hash_key(raw), raw[:12] + "..."


def extract_bearer_token(request: Request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

async def verify_virtual_key(request: Request) -> dict:
    """Validate the virtual API key and return key info.

    For admin routes, accepts GATEWAY_ADMIN_KEY.
    For proxy routes, validates against virtual_keys table.
    """
    token = extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    # Admin key check
    if token == config.ADMIN_KEY:
        return {"id": None, "name": "admin", "is_admin": True}

    # Virtual key check
    key_hash = hash_key(token)
    key_info = await db.validate_key(key_hash)
    if not key_info:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not key_info.get("is_active"):
        raise HTTPException(status_code=401, detail="Key is deactivated")

    # Check token limit
    if not await db.check_token_limit(key_info["id"]):
        raise HTTPException(status_code=429, detail="Token limit exceeded")

    return {**key_info, "is_admin": False}


async def verify_admin(request: Request) -> dict:
    """Verify admin key only."""
    info = await verify_virtual_key(request)
    if not info.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return info


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global app_config
    await db.connect()
    app_config = config.load_config()
    print(f"✓ LLM Gateway started — {config.DEFAULT_HOST}:{config.DEFAULT_PORT}")
    print(f"  Dashboard: http://{config.DEFAULT_HOST}:{config.DEFAULT_PORT}/")
    yield
    await db.close()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="LLM Gateway", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# Proxy endpoints (OpenAI-compatible)
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: Request):
    """Main proxy endpoint - OpenAI-compatible chat completions."""
    key_info = await verify_virtual_key(request)
    body = await request.json()
    model = body.get("model", "")
    provider = resolve_provider(model, app_config)

    # Check key model/provider filters
    if not key_info.get("is_admin"):
        pf = key_info.get("provider_filter")
        if pf and provider not in [p.strip() for p in pf.split(",")]:
            raise HTTPException(status_code=403, detail=f"Provider '{provider}' not allowed for this key")
        mf = key_info.get("model_filter")
        if mf and model not in [m.strip() for m in mf.split(",")]:
            raise HTTPException(status_code=403, detail=f"Model '{model}' not allowed for this key")

    stream = body.get("stream", False)

    try:
        response, meta = await proxy_chat_completions(body, provider, app_config)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        # Log the error
        await db.log_request(
            virtual_key_id=key_info.get("id"),
            request_id=str(id(request)),
            model=model,
            provider=provider,
            status="error",
            error_message=str(e)[:500],
            source_ip=request.client.host if request.client else None,
            request_body=_serialize_body(body),
        )
        raise HTTPException(status_code=502, detail=f"Upstream error: {str(e)[:200]}")

    if stream and isinstance(response, AsyncIterator):
        # Streaming: the response is an async generator. meta is a shared dict
        # that will be populated by the generator as it runs. We need to log
        # AFTER the generator finishes. We cannot use the generator's finally
        # block because Starlette may not await it properly.
        # Instead, we use Starlette's BackgroundTask which runs after the response
        # is fully sent (including the complete stream body).

        async def _log_after_stream():
            """Called by Starlette after the streaming response is fully sent."""
            try:
                await db.log_request(
                    virtual_key_id=key_info.get("id"),
                    request_id=meta["request_id"],
                    model=model,
                    provider=provider,
                    input_tokens=meta.get("input_tokens", 0),
                    output_tokens=meta.get("output_tokens", 0),
                    cache_read=meta.get("cache_read_tokens", 0),
                    cache_write=meta.get("cache_write_tokens", 0),
                    cost=meta.get("cost", 0),
                    latency_ms=meta.get("latency_ms", 0),
                    status=meta.get("status", "success"),
                    error_message=meta.get("error_message"),
                    source_ip=request.client.host if request.client else None,
                    request_body=_serialize_body(body),
                )
            except Exception as e:
                print(f"Stream logging error: {e}")

        from starlette.background import BackgroundTask
        return StreamingResponse(
            response,
            media_type="text/event-stream",
            background=BackgroundTask(_log_after_stream),
        )

    # Non-streaming: log immediately (meta already populated)
    await db.log_request(
        virtual_key_id=key_info.get("id"),
        request_id=meta["request_id"],
        model=model,
        provider=provider,
        input_tokens=meta.get("input_tokens", 0),
        output_tokens=meta.get("output_tokens", 0),
        cache_read=meta.get("cache_read_tokens", 0),
        cache_write=meta.get("cache_write_tokens", 0),
        cost=meta.get("cost", 0),
        latency_ms=meta.get("latency_ms", 0),
        status=meta.get("status", "success"),
        error_message=meta.get("error_message"),
        source_ip=request.client.host if request.client else None,
        request_body=_serialize_body(body),
        response_body=_serialize_body(response),
    )

    return JSONResponse(content=response)


@app.get("/v1/models")
@app.get("/models")
async def list_models():
    """List available models."""
    models = []
    seen = set()
    # 1. Explicitly configured in config.yaml (always shown, even without API keys)
    for model_name in app_config.get("models", {}):
        if model_name not in seen:
            models.append({"id": model_name, "object": "model", "created": 0, "owned_by": "llm-gateway"})
            seen.add(model_name)
    # 2. Have a working API key for their auto-detected provider
    for name in PRICING:
        if name not in seen:
            provider = _infer_provider(name)
            if config.get_provider_api_key(provider):
                models.append({"id": name, "object": "model", "created": 0, "owned_by": "llm-gateway"})
                seen.add(name)
    return {"object": "list", "data": models}


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/health/providers")
async def health_providers():
    """Check connectivity to all configured upstream providers.

    Returns per-provider status: reachable/unreachable/error with latency.
    Uses lightweight endpoints (GET /models or equivalent) to verify
    connectivity without consuming tokens.
    """
    results = {}
    provider_cfg = app_config.get("providers", {})
    model_routes = app_config.get("models", {})

    # Discover all active providers from config routes + env keys
    active_providers: set[str] = set()
    for _model, route in model_routes.items():
        if isinstance(route, dict):
            active_providers.add(route.get("provider", ""))
        elif isinstance(route, str):
            active_providers.add(route)
    for prov_name in config.PROVIDER_BASE_URLS:
        if config.get_provider_api_key(prov_name):
            active_providers.add(prov_name)
    for prov_name in provider_cfg:
        active_providers.add(prov_name)
    active_providers.discard("")

    for provider in sorted(active_providers):
        base_url = provider_cfg.get(provider, {})
        if isinstance(base_url, dict):
            base_url = base_url.get("base_url", "")
        if not base_url:
            base_url = config.PROVIDER_BASE_URLS.get(provider, "")

        if not base_url:
            results[provider] = {"status": "no_url", "error": "No base URL configured"}
            continue

        api_key = _get_api_key(provider, app_config)

        # Build a lightweight health-check URL
        if provider == "anthropic":
            check_url = f"{base_url}/v1/models"
            headers = {"x-api-key": api_key or "", "anthropic-version": "2023-06-01"}
        else:
            # OpenAI-compatible: /models is the standard lightweight endpoint
            check_url = f"{base_url}/models"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

        import time as _time
        start = _time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(check_url, headers=headers)
                latency_ms = (_time.monotonic() - start) * 1000
                if resp.status_code < 500:
                    results[provider] = {
                        "status": "reachable",
                        "status_code": resp.status_code,
                        "latency_ms": round(latency_ms, 1),
                    }
                else:
                    results[provider] = {
                        "status": "error",
                        "status_code": resp.status_code,
                        "latency_ms": round(latency_ms, 1),
                        "error": resp.text[:200],
                    }
        except Exception as e:
            latency_ms = (_time.monotonic() - start) * 1000
            results[provider] = {
                "status": "unreachable",
                "latency_ms": round(latency_ms, 1),
                "error": str(e)[:200],
            }

    return {"providers": results}

@app.post("/api/auth/verify")
async def auth_verify(request: Request):
    """Verify admin credentials. Returns success if valid."""
    try:
        admin = await verify_admin(request)
        return {"valid": True, "name": admin.get("name", "admin")}
    except HTTPException:
        return JSONResponse(content={"valid": False}, status_code=401)



# ---------------------------------------------------------------------------
# Admin API — key management
# ---------------------------------------------------------------------------

@app.post("/admin/keys")
async def create_key(request: Request, admin: dict = Depends(verify_admin)):
    """Create a new virtual API key."""
    body = await request.json()
    name = body.get("name", "unnamed")
    provider_filter = body.get("provider_filter")  # e.g. "openai,anthropic"
    model_filter = body.get("model_filter")         # e.g. "gpt-4o,claude-sonnet-4-5-20250514"
    token_limit = body.get("token_limit")            # e.g. 1_000_000

    raw_key, key_hash_val, key_prefix = generate_key()
    key_id = await db.create_key(
        name=name,
        key_hash=key_hash_val,
        key_prefix=key_prefix,
        provider_filter=provider_filter,
        model_filter=model_filter,
        token_limit=token_limit,
    )

    return {
        "id": key_id,
        "name": name,
        "key": raw_key,  # Only time the full key is returned
        "key_prefix": key_prefix,
        "provider_filter": provider_filter,
        "model_filter": model_filter,
        "token_limit": token_limit,
    }


@app.get("/admin/keys")
async def list_keys(admin: dict = Depends(verify_admin)):
    """List all virtual keys (without revealing full key)."""
    keys = await db.list_keys()
    # Remove hash from response
    for k in keys:
        k.pop("key_hash", None)
    return {"keys": keys}


@app.delete("/admin/keys/{key_id}")
async def delete_key(key_id: int, admin: dict = Depends(verify_admin)):
    """Delete a virtual key."""
    ok = await db.delete_key(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"ok": True}


@app.post("/admin/keys/{key_id}/deactivate")
async def deactivate_key(key_id: int, admin: dict = Depends(verify_admin)):
    """Deactivate a virtual key (keeps it but disables it)."""
    ok = await db.deactivate_key(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"ok": True}


@app.post("/admin/keys/{key_id}/reactivate")
async def reactivate_key(key_id: int, admin: dict = Depends(verify_admin)):
    """Reactivate a deactivated virtual key."""
    ok = await db.reactivate_key(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Stats API
# ---------------------------------------------------------------------------

@app.get("/api/stats/summary")
async def stats_summary(request: Request, admin: dict = Depends(verify_admin)):
    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")
    return await db.get_summary(date_from, date_to)


@app.get("/api/stats/models")
async def stats_models(request: Request, admin: dict = Depends(verify_admin)):
    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")
    return {"models": await db.get_model_stats(date_from, date_to)}


@app.get("/api/stats/providers")
async def stats_providers(request: Request, admin: dict = Depends(verify_admin)):
    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")
    return {"providers": await db.get_provider_stats(date_from, date_to)}


@app.get("/api/stats/daily")
async def stats_daily(request: Request, admin: dict = Depends(verify_admin)):
    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")
    key_id = _int_param(request, "key_id")
    return {"daily": await db.get_daily_stats(date_from, date_to, key_id)}


@app.get("/api/stats/hourly")
async def stats_hourly(request: Request, admin: dict = Depends(verify_admin)):
    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")
    key_id = _int_param(request, "key_id")
    return {"hourly": await db.get_hourly_stats(date_from, date_to, key_id)}


@app.get("/api/requests")
async def list_requests(request: Request, admin: dict = Depends(verify_admin)):
    key_id = _int_param(request, "key_id")
    model = request.query_params.get("model")
    provider = request.query_params.get("provider")
    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")
    limit = _int_param(request, "limit", 50)
    offset = _int_param(request, "offset", 0)
    reqs, total = await db.get_requests(key_id, model, provider, date_from, date_to, limit, offset)
    return {"requests": reqs, "total": total, "limit": limit, "offset": offset}


@app.get("/api/requests/{request_id}")
async def get_request(request_id: int, admin: dict = Depends(verify_admin)):
    """Return a single request with full request/response bodies."""
    row = await db.get_request(request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    return row


# ---------------------------------------------------------------------------
# Model Management API
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config(admin: dict = Depends(verify_admin)):
    """Get current config (models and providers)."""
    return {
        "models": app_config.get("models", {}),
        "providers": app_config.get("providers", {}),
    }


@app.post("/api/config")
async def update_config(data: dict, admin: dict = Depends(verify_admin)):
    """Update config (models and providers)."""
    import yaml
    
    new_config = {
        "models": data.get("models", {}),
        "providers": data.get("providers", {}),
    }
    
    config_path = os.getenv("GATEWAY_CONFIG", "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump(new_config, f, default_flow_style=False, sort_keys=False)
    
    # Reload config
    global app_config
    app_config = config.load_config(config_path)
    
    return {"message": "Config updated successfully"}


@app.delete("/api/providers/{provider_name}")
async def delete_provider(provider_name: str, admin: dict = Depends(verify_admin)):
    """Delete a provider from config."""
    if provider_name in config.PROVIDER_BASE_URLS:
        raise HTTPException(status_code=400, detail="Cannot delete built-in provider")
    
    new_config = app_config.copy()
    if "providers" in new_config and provider_name in new_config["providers"]:
        del new_config["providers"][provider_name]
    
    import yaml
    config_path = os.getenv("GATEWAY_CONFIG", "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump(new_config, f, default_flow_style=False, sort_keys=False)
    
    global app_config
    app_config = config.load_config(config_path)
    
    return {"message": f"Provider {provider_name} deleted"}


@app.delete("/api/models/{model_name}")
async def delete_model(model_name: str, admin: dict = Depends(verify_admin)):
    """Delete a model from config."""
    new_config = app_config.copy()
    if "models" in new_config and model_name in new_config["models"]:
        del new_config["models"][model_name]
    
    import yaml
    config_path = os.getenv("GATEWAY_CONFIG", "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump(new_config, f, default_flow_style=False, sort_keys=False)
    
    global app_config
    app_config = config.load_config(config_path)
    
    return {"message": f"Model {model_name} deleted"}


# ---------------------------------------------------------------------------
# Dashboard (HTML)
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "static")), name="static")


@app.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LLM Gateway - Lightweight LLM proxy")
    parser.add_argument("--host", default=config.DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    parser.add_argument("--config", default=config.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", default=config.DEFAULT_DB_PATH)
    args = parser.parse_args()

    # Override globals
    if args.db:
        global db
        db = Database(args.db)

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
