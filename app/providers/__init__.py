"""Provider adapters — route requests to the correct upstream LLM API.

Each provider adapter handles:
  - Building the correct URL
  - Setting the right auth headers
  - Translating the request/response format if needed
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator

import httpx

from app import config


# ---------------------------------------------------------------------------
# Pricing data (per 1M tokens) — keep in sync with provider pricing pages
# Format: { "model-name": { "input": float, "output": float, "cache_read": float } }
# cache_read is the discounted rate for cached input tokens (OpenAI/Anthropic)
# ---------------------------------------------------------------------------

PRICING: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00, "cache_read": 1.25},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075},
    "gpt-4.1": {"input": 2.00, "output": 8.00, "cache_read": 0.50},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60, "cache_read": 0.10},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40, "cache_read": 0.025},
    "gpt-5.1": {"input": 5.00, "output": 15.00, "cache_read": 2.50},
    "gpt-5.1-mini": {"input": 1.50, "output": 6.00, "cache_read": 0.375},
    "o3": {"input": 10.00, "output": 40.00, "cache_read": 2.50},
    "o3-mini": {"input": 1.10, "output": 4.40, "cache_read": 0.275},
    "o4-mini": {"input": 1.10, "output": 4.40, "cache_read": 0.275},
    # Anthropic
    "claude-sonnet-4-5-20250514": {"input": 3.00, "output": 15.00, "cache_read": 0.30},
    "claude-opus-4-20250514": {"input": 15.00, "output": 75.00, "cache_read": 1.50},
    "claude-haiku-4-5-20241022": {"input": 0.80, "output": 4.00, "cache_read": 0.08},
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00, "cache_read": 0.08},
    # Google
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "cache_read": 0.315},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60, "cache_read": 0.0375},
    # DeepSeek
    "deepseek-chat": {"input": 0.27, "output": 1.10, "cache_read": 0.07},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19, "cache_read": 0.14},
    # Groq
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    # Mistral
    "mistral-small": {"input": 0.20, "output": 0.60},
    "mistral-medium": {"input": 0.80, "output": 2.40},
    "mistral-large": {"input": 2.00, "output": 6.00},
    # llama-swap / Self-hosted models (set to 0 for free local inference)
    # Adjust these values if you want to track electricity/compute costs
    "Qwen3.5-27B": {"input": 0.00, "output": 0.00},
    "Qwen3.5-9B": {"input": 0.00, "output": 0.00},
    "Qwen3.5-4B": {"input": 0.00, "output": 0.00},
    "GLM-OCR": {"input": 0.00, "output": 0.00},
    "MiniMax-M2.7": {"input": 0.00, "output": 0.00},
}


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    config: dict = None,
) -> float:
    """Calculate cost in USD for a request.
    
    Args:
        model: Model name
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        cache_read_tokens: Number of cached tokens (discounted)
        config: Optional config dict with model pricing overrides
    
    Returns:
        Cost in USD
    """
    # Check for config override first
    if config:
        model_routes = config.get("models", {})
        if model in model_routes:
            model_config = model_routes[model]
            if isinstance(model_config, dict):
                # Support both nested pricing format and flat format (from UI)
                prices = None
                
                # Check for nested pricing format (from config.yaml)
                if "pricing" in model_config:
                    prices = model_config["pricing"]
                # Check for flat format (from UI)
                elif "input" in model_config or "output" in model_config:
                    prices = {
                        "input": model_config.get("input", 0),
                        "output": model_config.get("output", 0),
                        "cache_read": model_config.get("cache_read", 0),
                    }
                
                if prices:
                    input_price = prices.get("input", 0) / 1_000_000
                    output_price = prices.get("output", 0) / 1_000_000
                    cache_price = prices.get("cache_read", prices.get("input", 0) * 0.5) / 1_000_000
                    regular_input = max(0, input_tokens - cache_read_tokens)
                    return (regular_input * input_price) + (cache_read_tokens * cache_price) + (output_tokens * output_price)
    
    # Fall back to default pricing
    prices = PRICING.get(model, {"input": 0, "output": 0})
    input_price = prices.get("input", 0) / 1_000_000
    output_price = prices.get("output", 0) / 1_000_000
    cache_price = prices.get("cache_read", prices.get("input", 0) * 0.5) / 1_000_000

    regular_input = max(0, input_tokens - cache_read_tokens)
    return (regular_input * input_price) + (cache_read_tokens * cache_price) + (output_tokens * output_price)


def resolve_provider(model: str, cfg: dict) -> str:
    """Determine which provider handles a model.

    Checks config.yaml model routing first, then falls back to heuristics.
    """
    # Check config model routing
    model_routes = cfg.get("models", {})
    if model in model_routes:
        route = model_routes[model]
        if isinstance(route, dict):
            return route.get("provider", _infer_provider(model))
        return str(route)

    return _infer_provider(model)


def _infer_provider(model: str) -> str:
    """Guess provider from model name prefix."""
    model_lower = model.lower()
    if model_lower.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-", "dall")):
        return "openai"
    if model_lower.startswith("claude-"):
        return "anthropic"
    if model_lower.startswith("gemini-"):
        return "gemini"
    if model_lower.startswith("deepseek-"):
        return "deepseek"
    if model_lower.startswith(("llama-", "mixtral-")) and "groq" not in model_lower:
        return "openrouter"  # default open-weight models to openrouter
    if model_lower.startswith("mistral-"):
        return "mistral"
    if model_lower.startswith("qwen"):
        return "openrouter"
    # Default to openrouter for unknown models
    return "openrouter"


def _get_base_url(provider: str, cfg: dict) -> str:
    """Get base URL for a provider, allowing config overrides."""
    provider_cfg = cfg.get("providers", {}).get(provider, {})
    if isinstance(provider_cfg, dict) and "base_url" in provider_cfg:
        return provider_cfg["base_url"]
    return config.PROVIDER_BASE_URLS.get(provider, config.PROVIDER_BASE_URLS["openrouter"])


def _get_api_key(provider: str, cfg: dict) -> str | None:
    """Get API key for a provider — config override or env var."""
    provider_cfg = cfg.get("providers", {}).get(provider, {})
    if isinstance(provider_cfg, dict) and "api_key" in provider_cfg:
        return provider_cfg["api_key"]
    return config.get_provider_api_key(provider)


# ---------------------------------------------------------------------------
# Proxy functions
# ---------------------------------------------------------------------------


async def proxy_chat_completions(
    body: dict[str, Any],
    provider: str,
    cfg: dict,
    timeout: float = 120.0,
) -> tuple[dict[str, Any] | AsyncIterator[bytes], dict[str, Any]]:
    """Proxy a chat completions request to the correct provider.

    Returns (response_or_stream, metadata) where metadata contains:
        provider, model, input_tokens, output_tokens, cache_read_tokens,
        cache_write_tokens, cost, latency_ms, status, error_message, request_id
    """
    request_id = str(uuid.uuid4())
    model = body.get("model", "unknown")
    stream = body.get("stream", False)
    base_url = _get_base_url(provider, cfg)
    api_key = _get_api_key(provider, cfg)

    if not api_key:
        raise ValueError(f"No API key configured for provider '{provider}'")

    # Build headers based on provider
    headers = _build_headers(provider, api_key)

    # Build the request body — Anthropic needs different format
    url, req_body = _build_request(provider, base_url, body)

    start = time.monotonic()
    meta: dict[str, Any] = {
        "request_id": request_id,
        "provider": provider,
        "model": model,
        "status": "success",
        "error_message": None,
    }

    if stream:
        # Request usage data in streaming response (OpenAI stream_options spec)
        # Most providers (llama.cpp, OpenAI, etc.) support this
        req_body.setdefault("stream_options", {"include_usage": True})

        # For streaming, create client outside context manager so it stays alive during streaming
        client = httpx.AsyncClient(timeout=timeout)
        return await _handle_stream(client, url, headers, req_body, model, provider, start, meta, cfg)
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=req_body)
        latency_ms = (time.monotonic() - start) * 1000

        if resp.status_code >= 400:
            error_text = resp.text[:500]
            meta.update(latency_ms=latency_ms, status="error", error_message=error_text)
            return {"error": error_text, "status_code": resp.status_code}, meta

        resp_json = resp.json()
        usage = resp_json.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        cache_read = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0) if isinstance(usage.get("prompt_tokens_details"), dict) else 0

        cost = calculate_cost(model, input_tokens, output_tokens, cache_read, cfg)
        
        # Use upstream timings when available (llama.cpp sends prompt_ms + predicted_ms)
        timings = resp_json.get("timings", {})
        if timings:
            upstream_latency = timings.get("prompt_ms", 0) + timings.get("predicted_ms", 0)
            tps = timings.get("predicted_per_second")
            ttft = timings.get("prompt_ms", latency_ms)
            # Prefer upstream latency over wall-clock
            if upstream_latency > 0:
                latency_ms = upstream_latency
        else:
            tps = None
            ttft = latency_ms
        
        # Fallback: calculate TPS using total latency
        if tps is None and latency_ms > 0 and output_tokens > 0:
            tps = output_tokens / (latency_ms / 1000)
        
        # For non-streaming: TTFT = prompt processing time (or full latency as fallback)
        meta.update(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=0,
            cost=cost,
            latency_ms=latency_ms,
            time_to_first_token_ms=ttft,
            tokens_per_second=tps,
        )
        return resp_json, meta


async def _handle_stream(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    body: dict,
    model: str,
    provider: str,
    start: float,
    meta: dict,
    cfg: dict,
) -> tuple[AsyncIterator[bytes], dict]:
    """Handle streaming responses — transparent byte-for-byte pass-through.

    Extracts usage silently for DB logging without modifying what the client sees.
    The upstream response is sent exactly as-is.
    """
    tps = None

    async def stream_generator():
        nonlocal tps
        accumulated = b""
        usage_data = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0}
        response_parts = []
        parsed = {}
        tps = None

        try:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    error_body = await resp.aread()
                    meta["status"] = "error"
                    meta["error_message"] = error_body.decode()[:500]
                    meta["latency_ms"] = (time.monotonic() - start) * 1000
                    meta.update(
                        input_tokens=0,
                        output_tokens=0,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                        cost=0.0,
                        latency_ms=meta["latency_ms"],
                        time_to_first_token_ms=0,
                        tokens_per_second=None,
                    )
                    yield json.dumps({"error": meta["error_message"]}).encode()
                    raise ValueError(meta["error_message"])

            # Stream raw bytes from upstream — no transformation
                async for chunk in resp.aiter_bytes():
                    # Track TTFT on first byte received
                    if meta.get("time_to_first_token_ms") is None:
                        meta["time_to_first_token_ms"] = (time.monotonic() - start) * 1000

                    # Accumulate to extract usage without modifying what's sent
                    accumulated += chunk

                    # Process complete SSE lines for usage extraction
                    while b"\n\n" in accumulated:
                        line, accumulated = accumulated.split(b"\n\n", 1)
                        if not line.startswith(b"data: "):
                            continue
                        data = line[6:]
                        if data == b"[DONE]":
                            break
                        try:
                            parsed = json.loads(data)
                            if "usage" in parsed:
                                usage_data["input_tokens"] = parsed["usage"].get("prompt_tokens", 0)
                                usage_data["output_tokens"] = parsed["usage"].get("completion_tokens", 0)
                                cd = parsed["usage"].get("prompt_tokens_details", {})
                                if isinstance(cd, dict):
                                    usage_data["cache_read"] = cd.get("cached_tokens", 0)
                                if "timings" in parsed:
                                    tps = parsed["timings"].get("predicted_per_second")
                            choices = parsed.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content") or delta.get("reasoning_content") or ""
                                response_parts.append(content)
                        except json.JSONDecodeError:
                            continue

                    # Pass raw bytes to client unchanged
                    yield chunk

                # Handle any remaining accumulated data after stream ends
                if accumulated:
                    line = accumulated.decode("utf-8", errors="replace")
                    if line.startswith("data: "):
                        data = line[6:]
                        if data != "[DONE]":
                            try:
                                parsed = json.loads(data)
                                if "usage" in parsed:
                                    usage_data["input_tokens"] = parsed["usage"].get("prompt_tokens", usage_data["input_tokens"])
                                    usage_data["output_tokens"] = parsed["usage"].get("completion_tokens", usage_data["output_tokens"])
                                choices = parsed.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content") or delta.get("reasoning_content") or ""
                                    response_parts.append(content)
                            except json.JSONDecodeError:
                                pass

            # Build response_body for DB logging
            response_body = {
                "id": parsed.get("id", ""),
                "object": "chat.completion",
                "created": parsed.get("created", 0),
                "model": parsed.get("model", model),
                "choices": [{"message": {"content": "".join(response_parts)}, "finish_reason": parsed.get("finish_reason", "")}],
                "usage": usage_data,
            }
            meta["response_body"] = json.dumps(response_body)

            # Use upstream timings when available
            upstream_timings = parsed.get("timings", {})
            if upstream_timings:
                latency_ms = upstream_timings.get("prompt_ms", 0) + upstream_timings.get("predicted_ms", 0)
                if tps is None:
                    tps = upstream_timings.get("predicted_per_second")
            else:
                latency_ms = (time.monotonic() - start) * 1000

            if tps is None and latency_ms > 0 and usage_data["output_tokens"] > 0:
                tps = usage_data["output_tokens"] / (latency_ms / 1000)

            meta.update(
                input_tokens=usage_data["input_tokens"],
                output_tokens=usage_data["output_tokens"],
                cache_read_tokens=usage_data["cache_read"],
                cache_write_tokens=0,
                cost=calculate_cost(model, usage_data["input_tokens"], usage_data["output_tokens"], usage_data["cache_read"], cfg),
                latency_ms=latency_ms,
                time_to_first_token_ms=meta.get("time_to_first_token_ms", 0),
                tokens_per_second=tps,
            )
        except Exception as e:
            meta["status"] = "error"
            meta["error_message"] = str(e)[:500]
            meta["latency_ms"] = (time.monotonic() - start) * 1000
            yield json.dumps({"error": str(e)}).encode()
            raise

    return stream_generator(), meta


def _build_headers(provider: str, api_key: str) -> dict[str, str]:
    """Build request headers for the given provider."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    # Some providers don't use Authorization header
    if provider in ("anthropic", "gemini"):
        del headers["Authorization"]
        if provider == "anthropic":
            headers["x-api-key"] = api_key
        else:
            headers["google-api-key"] = api_key

    return headers


def _build_request(provider: str, base_url: str, body: dict) -> tuple[str, dict]:
    """Build the URL and request body for the provider.

    For OpenAI-compatible providers, pass through with minimal changes.
    For Anthropic, translate the format.
    """
    model = body.get("model", "")

    if provider == "anthropic":
        url = f"{base_url}/v1/messages"
        req_body = _openai_to_anthropic(body)
        return url, req_body

    if provider == "gemini":
        url = f"{base_url}/models/{model}:generateContent?key={_get_api_key('gemini', {})}"
        req_body = _openai_to_gemini(body)
        return url, req_body

    # OpenAI-compatible
    url = f"{base_url}/chat/completions"
    return url, body


def _openai_to_anthropic(body: dict) -> dict:
    """Convert OpenAI chat completions format to Anthropic messages format."""
    messages = body.get("messages", [])
    system_content = None
    filtered_messages = []

    for msg in messages:
        if msg.get("role") == "system":
            system_content = msg.get("content", "")
        else:
            filtered_messages.append(msg)

    anthropic_body: dict[str, Any] = {
        "model": body.get("model", ""),
        "messages": filtered_messages,
        "max_tokens": body.get("max_tokens", 4096),
    }
    if system_content:
        anthropic_body["system"] = system_content
    if body.get("temperature") is not None:
        anthropic_body["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        anthropic_body["top_p"] = body["top_p"]
    if body.get("stop"):
        anthropic_body["stop_sequences"] = body["stop"] if isinstance(body["stop"], list) else [body["stop"]]
    if body.get("stream"):
        anthropic_body["stream"] = True

    return anthropic_body


def _openai_to_gemini(body: dict) -> dict:
    """Convert OpenAI chat completions format to Google Gemini format."""
    messages = body.get("messages", [])
    contents = []

    # Build conversation history
    for msg in messages:
        role = "user" if msg.get("role") == "user" else ("model" if msg.get("role") == "assistant" else "system")
        contents.append({"role": role, "parts": [msg.get("content", "")]})

    gemini_body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": body.get("temperature", 0.7),
            "top_p": body.get("top_p", 1.0),
            "top_k": body.get("top_k", 40),
            "max_output_tokens": body.get("max_tokens", 4096),
        },
    }

    return gemini_body

