"""Registers a Gemini variant that adds AI_GATEWAY_HEADERS to every request.

ADK's native Gemini class (used for bare model names like "gemini-2.0-flash",
as opposed to litellm-routed "vertex_ai/gemini-..." names) talks to
google.genai directly. Unlike litellm, google.genai has no global-headers env
var, so a gateway that needs more than just GOOGLE_API_KEY needs this small
override — LLMRegistry.new_llm(model) only ever calls cls(model=model), so
there's no per-call way to inject extra_headers otherwise.

Only registered when AI_GATEWAY_HEADERS is set (see worker.py); a no-op
otherwise, at which point stock Gemini behavior is unchanged.
"""

import os
from functools import cached_property
from typing import Any

from google.adk.models import Gemini, LLMRegistry
from google.genai import Client, types


class _GatewayGemini(Gemini):
    @cached_property
    def api_client(self) -> Client:
        base_url, api_version = self._base_url_and_api_version
        kwargs_for_http_options: dict[str, Any] = {
            "headers": {**self._tracking_headers(), **_gateway_headers()},
            "retry_options": self.retry_options,
            "base_url": base_url,
        }
        if api_version:
            kwargs_for_http_options["api_version"] = api_version

        kwargs: dict[str, Any] = {"http_options": types.HttpOptions(**kwargs_for_http_options)}
        if self.model.startswith("projects/"):
            kwargs["enterprise"] = True
        if self.client_kwargs:
            kwargs.update(self.client_kwargs)
        return Client(**kwargs)


def _gateway_headers() -> dict[str, str]:
    raw = os.environ.get("AI_GATEWAY_HEADERS", "")
    return dict(pair.split("=", 1) for pair in raw.split(",") if pair)


def register_gateway_gemini_if_configured() -> None:
    if os.environ.get("AI_GATEWAY_HEADERS"):
        LLMRegistry.register(_GatewayGemini)
