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
    "claude-haiku-4-5-20250514": {"input": 0.80, "output": 4.00, "cache_read": 0.08},
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
}


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    """Calculate cost in USD for a request."""
    prices = PRICING.get(model, {"input": 0, "output": 0})
    input_price = prices.get("input", 0) / 1_000_000
    output_price = prices.get("output", 0) / 1_000_000
    cache_price = prices.get("cache_read", prices.get("input", 0) / 1_000_000 * 0.5) / 1_000_000

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

    # Build headers — local/self-hosted providers may not need API keys
    headers = _build_headers(provider, api_key or "")

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
        # Client must stay alive for streaming — closed in _handle_stream finally block
        client = httpx.AsyncClient(timeout=timeout)
        return await _handle_stream(client, url, headers, req_body, model, provider, start, meta)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=req_body)
        latency_ms = (time.monotonic() - start) * 1000

        if resp.status_code >= 400:
            error_text = resp.text[:500]
            meta.update(latency_ms=latency_ms, status="error", error_message=error_text)
            return {"error": error_text, "status_code": resp.status_code}, meta

        resp_json = resp.json()

        # Translate provider-specific responses back to OpenAI format
        if provider == "anthropic":
            resp_json = _anthropic_to_openai(resp_json, model)
        elif provider == "gemini":
            resp_json = _gemini_to_openai(resp_json, model)

        usage = resp_json.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        cache_read = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0) if isinstance(usage.get("prompt_tokens_details"), dict) else 0

        cost = calculate_cost(model, input_tokens, output_tokens, cache_read)
        # For non-streaming: TTFT = full latency, TPS = output_tokens / (latency_ms / 1000)
        tps = output_tokens / (latency_ms / 1000) if latency_ms > 0 and output_tokens > 0 else None
        meta.update(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=0,
            cost=cost,
            latency_ms=latency_ms,
            time_to_first_token_ms=latency_ms,
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
) -> tuple[AsyncIterator[bytes], dict]:
    """Handle streaming responses — pass through SSE chunks."""
    async def stream_generator():
        usage_data = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0}
        first_token_time = None
        try:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    error_body = await resp.aread()
                    meta["status"] = "error"
                    meta["error_message"] = error_body.decode()[:500]
                    meta["latency_ms"] = (time.monotonic() - start) * 1000
                    yield f'data: {{"error": {json.dumps(error_body.decode()[:500])}, "status_code": {resp.status_code}}}\n\n'.encode()
                    yield b"data: [DONE]\n\n"
                    return

                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        chunk = line[6:]
                        if chunk.strip() == "[DONE]":
                            # Calculate cost from accumulated usage
                            latency_ms = (time.monotonic() - start) * 1000
                            cost = calculate_cost(model, usage_data["input_tokens"], usage_data["output_tokens"], usage_data["cache_read"])
                            # Calculate TPS: output tokens / generation duration in seconds
                            end_time = time.monotonic()
                            out_tokens = usage_data["output_tokens"]
                            if first_token_time is not None and out_tokens > 0:
                                gen_duration_s = (end_time - first_token_time)
                                tps = out_tokens / gen_duration_s if gen_duration_s > 0 else None
                            else:
                                tps = None
                            # TTFT: time from start to first content token
                            ttft_ms = (first_token_time - start) * 1000 if first_token_time is not None else None
                            meta.update(
                                input_tokens=usage_data["input_tokens"],
                                output_tokens=usage_data["output_tokens"],
                                cache_read_tokens=usage_data["cache_read"],
                                cost=cost,
                                latency_ms=latency_ms,
                                time_to_first_token_ms=ttft_ms,
                                tokens_per_second=tps,
                            )
                            yield b"data: [DONE]\n\n"
                            return
                        # Try to extract usage from Anthropic message_start/message_delta
                        try:
                            parsed = json.loads(chunk)
                            # Anthropic streaming usage
                            if provider == "anthropic":
                                if parsed.get("type") == "message_start":
                                    u = parsed.get("message", {}).get("usage", {})
                                    usage_data["input_tokens"] = u.get("input_tokens", 0)
                                    usage_data["cache_read"] = u.get("cache_read_input_tokens", 0)
                                elif parsed.get("type") == "message_delta":
                                    u = parsed.get("usage", {})
                                    usage_data["output_tokens"] += u.get("output_tokens", 0)
                                elif parsed.get("type") == "content_block_delta":
                                    # First content-bearing chunk for Anthropic
                                    if first_token_time is None:
                                        delta = parsed.get("delta", {})
                                        if delta.get("type") == "text_delta" and delta.get("text"):
                                            first_token_time = time.monotonic()
                            else:
                                # OpenAI-compatible streaming usage (in last chunk)
                                u = parsed.get("usage", {})
                                if u:
                                    usage_data["input_tokens"] = u.get("prompt_tokens", usage_data["input_tokens"])
                                    usage_data["output_tokens"] = u.get("completion_tokens", usage_data["output_tokens"])
                                    cd = u.get("prompt_tokens_details", {})
                                    if isinstance(cd, dict):
                                        usage_data["cache_read"] = cd.get("cached_tokens", usage_data["cache_read"])
                                # Fallback: llama.cpp sends timings with prompt_n/predicted_n
                                if usage_data["input_tokens"] == 0:
                                    timings = parsed.get("timings", {})
                                    if timings:
                                        usage_data["input_tokens"] = timings.get("prompt_n", 0)
                                        usage_data["output_tokens"] = timings.get("predicted_n", 0)
                                # Detect first content token for OpenAI-compatible
                                if first_token_time is None:
                                    choices = parsed.get("choices", [])
                                    if choices:
                                        delta = choices[0].get("delta", {})
                                        content = delta.get("content")
                                        if content is not None and delta.get("role") is None:
                                            first_token_time = time.monotonic()
                        except (json.JSONDecodeError, KeyError, TypeError):
                            pass
                    yield (line + "\n\n").encode()
        except Exception as e:
            meta["status"] = "error"
            meta["error_message"] = str(e)[:500]
            meta["latency_ms"] = (time.monotonic() - start) * 1000
            yield f'data: {{"error": {json.dumps(str(e))}}}\n\n'.encode()
        finally:
            await client.aclose()

    return stream_generator(), meta


def _build_headers(provider: str, api_key: str) -> dict[str, str]:
    """Build request headers for the given provider."""
    if provider == "anthropic":
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    if provider == "gemini":
        return {
            "content-type": "application/json",
        }
    # OpenAI-compatible (openai, groq, together, deepseek, mistral, xai, openrouter, etc.)
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


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
    """Convert OpenAI chat completions format to Gemini format."""
    messages = body.get("messages", [])
    contents = []
    for msg in messages:
        role = "model" if msg.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})

    gemini_body: dict[str, Any] = {"contents": contents}
    if body.get("temperature") is not None:
        gemini_body.setdefault("generationConfig", {})["temperature"] = body["temperature"]
    if body.get("max_tokens") is not None:
        gemini_body.setdefault("generationConfig", {})["maxOutputTokens"] = body["max_tokens"]

    return gemini_body


def _anthropic_to_openai(resp: dict, model: str) -> dict:
    """Convert an Anthropic messages response to OpenAI chat completion format."""
    # Extract text from content blocks
    content_blocks = resp.get("content", [])
    text_parts = []
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif isinstance(block, str):
            text_parts.append(block)
    content = "\n".join(text_parts) if text_parts else ""

    # Map stop reasons
    stop_reason = resp.get("stop_reason")
    finish_map = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
    }
    finish_reason = finish_map.get(stop_reason, "stop")

    # Usage
    a_usage = resp.get("usage", {})
    input_tokens = a_usage.get("input_tokens", 0)
    output_tokens = a_usage.get("output_tokens", 0)
    cache_read = a_usage.get("cache_read_input_tokens", 0)
    cache_write = a_usage.get("cache_creation_input_tokens", 0)

    return {
        "id": resp.get("id", f"chatcmpl-{uuid.uuid4().hex[:24]}"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": resp.get("model", model),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "prompt_tokens_details": {"cached_tokens": cache_read},
        },
    }


def _gemini_to_openai(resp: dict, model: str) -> dict:
    """Convert a Gemini generateContent response to OpenAI chat completion format."""
    candidates = resp.get("candidates", [])
    content = ""
    finish_reason = "stop"
    if candidates:
        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        text_parts = [p.get("text", "") for p in parts if "text" in p]
        content = "\n".join(text_parts)
        reason = candidate.get("finishReason", "")
        if reason == "MAX_TOKENS":
            finish_reason = "length"
        elif reason == "SAFETY":
            finish_reason = "content_filter"

    usage_meta = resp.get("usageMetadata", {})
    input_tokens = usage_meta.get("promptTokenCount", 0)
    output_tokens = usage_meta.get("candidatesTokenCount", 0)
    cached = usage_meta.get("cachedContentTokenCount", 0)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }
