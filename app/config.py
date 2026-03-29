"""Configuration loading and defaults."""

import os
from pathlib import Path
from typing import Optional
import yaml
from dotenv import load_dotenv

load_dotenv()

DEFAULT_CONFIG_PATH = os.getenv("GATEWAY_CONFIG", "config.yaml")
DEFAULT_DB_PATH = os.getenv("GATEWAY_DB", "data/gateway.db")
DEFAULT_HOST = os.getenv("GATEWAY_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("GATEWAY_PORT", "4000"))
ADMIN_KEY = os.getenv("GATEWAY_ADMIN_KEY", "sk-admin-change-me")


def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Load config.yaml with provider settings and model routing."""
    p = Path(path)
    if not p.exists():
        return {"providers": {}, "models": {}}
    with open(p) as f:
        return yaml.safe_load(f) or {}


def get_provider_api_key(provider: str) -> Optional[str]:
    """Resolve API key for a provider from environment variables."""
    env_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "groq": "GROQ_API_KEY",
        "together": "TOGETHER_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "xai": "XAI_API_KEY",
        "fireworks": "FIREWORKS_API_KEY",
        "perplexity": "PERPLEXITY_API_KEY",
    }
    env_var = env_map.get(provider.lower())
    if env_var:
        return os.getenv(env_var)
    # Fallback: GATEWAY_<PROVIDER>_KEY
    return os.getenv(f"GATEWAY_{provider.upper()}_KEY")


# Provider base URLs
PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "xai": "https://api.x.ai/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "perplexity": "https://api.perplexity.ai",
}
