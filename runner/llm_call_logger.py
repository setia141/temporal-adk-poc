"""Logs the exact outbound HTTP call litellm makes for every LLM invocation:
resolved URL, headers (values masked), and model — proof of what actually
went on the wire, independent of what gateway_litellm.py *intended* to send.

Registered once at worker startup (see worker.py); runs inside invoke_model
(a real Activity, not workflow code), so plain logging is fine here — no
replay-safety concerns like workflow.logger.
"""

import logging

import litellm
from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger("llm_call")


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-2:]}"


class _CallLogger(CustomLogger):
    def log_pre_api_call(self, model, messages, kwargs):
        additional_args = kwargs.get("additional_args") or {}
        api_base = additional_args.get("api_base", "(default)")
        headers = additional_args.get("headers") or {}
        masked = {k: _mask(v) if isinstance(v, str) else v for k, v in headers.items()}
        logger.info(
            "LLM HTTP request: model=%s url=%s headers=%s message_count=%d",
            model, api_base, masked, len(messages or []),
        )

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        logger.info(
            "LLM HTTP response: model=%s status=success duration=%.2fs",
            kwargs.get("model"), (end_time - start_time).total_seconds(),
        )

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        logger.info(
            "LLM HTTP response: model=%s status=FAILURE duration=%.2fs error=%s",
            kwargs.get("model"), (end_time - start_time).total_seconds(), response_obj,
        )


def register_llm_call_logger() -> None:
    litellm.callbacks = [*(litellm.callbacks or []), _CallLogger()]
