"""Registers a LiteLlm subclass that carries custom gateway config (base URL,
extra headers) through LLMRegistry resolution.

Why this exists: TemporalModel serializes only the model-name string into the
invoke_model activity, which rebuilds the LLM via LLMRegistry.new_llm(name) →
cls(model=name) — no constructor kwargs survive the activity boundary. So a
LiteLlm(api_base=..., extra_headers=...) built in workflow code can never reach
the actual call. Instead, this subclass re-registers itself for the same
model-name patterns litellm normally claims, baking the gateway config in at
construction time inside the worker process (where invoke_model runs).

Only registered when AI_GATEWAY_BASE_URL or AI_GATEWAY_HEADERS is set (see
worker.py); a no-op otherwise, leaving stock LiteLlm resolution unchanged.
"""

import logging
import os

from google.adk.models import LLMRegistry
from google.adk.models.lite_llm import LiteLlm

logger = logging.getLogger(__name__)

# Must exactly match the regex strings ADK pre-registers for LiteLlm
# (google/adk/models/__init__.py _LAZY_PROVIDERS) — LLMRegistry._register
# replaces an entry only when the regex string is identical; a merely
# equivalent pattern would be appended after the originals and never win.
_LITELLM_PATTERNS = [
    # Not in ADK's own litellm provider list (bare gemini-* names resolve to
    # the native Gemini class instead) — added here so gemini/... routes
    # through litellm and picks up the gateway config like every other provider.
    r"gemini/.*",
    r"openai/.*",
    r"azure/.*",
    r"azure_ai/.*",
    r"groq/.*",
    r"anthropic/.*",
    r"bedrock/.*",
    r"ollama/(?!gemma3).*",
    r"ollama_chat/.*",
    r"together_ai/.*",
    r"vertex_ai/.*",
    r"mistral/.*",
    r"deepseek/.*",
    r"fireworks_ai/.*",
    r"cohere/.*",
    r"databricks/.*",
    r"ai21/.*",
]


def _gateway_headers() -> dict[str, str]:
    raw = os.environ.get("AI_GATEWAY_HEADERS", "")
    return dict(pair.split("=", 1) for pair in raw.split(",") if pair)


class GatewayLiteLlm(LiteLlm):
    """LiteLlm with the org gateway's base URL and extra headers baked in,
    so LLMRegistry's name-only construction still produces a fully
    configured client."""

    def __init__(self, model: str, **kwargs):
        if base_url := os.environ.get("AI_GATEWAY_BASE_URL"):
            kwargs.setdefault("api_base", base_url)
        if headers := _gateway_headers():
            kwargs.setdefault("extra_headers", headers)
        if api_key := os.environ.get("AI_GATEWAY_API_KEY"):
            kwargs.setdefault("api_key", api_key)
        super().__init__(model=model, **kwargs)

    @classmethod
    def supported_models(cls) -> list[str]:
        return _LITELLM_PATTERNS


def register_gateway_litellm_if_configured() -> None:
    base_url = os.environ.get("AI_GATEWAY_BASE_URL")
    headers = _gateway_headers()
    if base_url or headers:
        # Header names only — values may be secrets.
        logger.info(
            "Custom gateway ACTIVE: base_url=%s header_names=%s — registering GatewayLiteLlm",
            base_url or "(not set — provider default)", sorted(headers),
        )
        LLMRegistry.register(GatewayLiteLlm)
    else:
        logger.info(
            "Custom gateway NOT configured (AI_GATEWAY_BASE_URL/AI_GATEWAY_HEADERS "
            "unset or commented) — model calls go to the provider's default endpoint"
        )
