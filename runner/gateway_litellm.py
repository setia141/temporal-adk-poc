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

import os

from google.adk.models import LLMRegistry
from google.adk.models.lite_llm import LiteLlm

# Must exactly match the regex strings ADK pre-registers for LiteLlm
# (google/adk/models/__init__.py _LAZY_PROVIDERS) — LLMRegistry._register
# replaces an entry only when the regex string is identical; a merely
# equivalent pattern would be appended after the originals and never win.
_LITELLM_PATTERNS = [
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
        super().__init__(model=model, **kwargs)

    @classmethod
    def supported_models(cls) -> list[str]:
        return _LITELLM_PATTERNS


def register_gateway_litellm_if_configured() -> None:
    if os.environ.get("AI_GATEWAY_BASE_URL") or os.environ.get("AI_GATEWAY_HEADERS"):
        LLMRegistry.register(GatewayLiteLlm)
