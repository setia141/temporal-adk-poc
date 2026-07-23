"""Helper for running a single-turn Google ADK agent backed by LiteLLM.

Kept separate from the agent activities so the ADK/LiteLLM wiring is
defined once and reused by every agent activity.
"""

import os
from dataclasses import dataclass

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .clarify_prompt import CLARIFY_CONVENTION, CLARIFY_PREFIX


@dataclass
class AgentRunResult:
    output: str
    needs_clarification: bool = False
    question: str = ""


def _gateway_headers() -> dict[str, str]:
    """Extra HTTP headers for a custom gateway, beyond what OPENAI_API_KEY /
    AZURE_API_KEY / etc. already send. Every provider (openai/, azure/,
    anthropic/, gemini/, vertex_ai/, ...) goes through this same LiteLlm(...)
    call, so one env var covers all of them — no per-provider wiring needed."""
    raw = os.environ.get("AI_GATEWAY_HEADERS", "")
    return dict(pair.split("=", 1) for pair in raw.split(",") if pair)


async def run_agent(
    name: str,
    instruction: str,
    prompt: str,
    allow_clarification: bool = True,
    image_bytes: bytes | None = None,
    image_mime_type: str = "",
) -> AgentRunResult:
    """Runs one ADK agent turn and returns its final text response.

    The agent's model is a LiteLlm wrapper, which routes the request through
    LiteLLM's OpenAI-compatible client. Point it at a LiteLLM gateway by
    setting OPENAI_API_BASE; otherwise it calls OpenAI directly.

    When allow_clarification is False, the agent is never allowed to pause
    the workflow to ask the user something — used for activities that run
    in parallel with another activity, since the workflow's clarification
    state only supports one pending question at a time.
    """
    model_name = os.environ.get("ADK_MODEL", "openai/gpt-4o-mini")

    agent = LlmAgent(
        name=name,
        model=LiteLlm(model=model_name, extra_headers=_gateway_headers() or None),
        instruction=instruction + CLARIFY_CONVENTION if allow_clarification else instruction,
    )

    app_name = "temporal-adk-poc"
    user_id = "poc-user"
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=app_name, user_id=user_id)
    runner = Runner(agent=agent, app_name=app_name, session_service=session_service)

    parts = [types.Part(text=prompt)]
    if image_bytes:
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type))
    content = types.Content(role="user", parts=parts)

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(p.text or "" for p in event.content.parts)

    if not allow_clarification:
        return AgentRunResult(output=final_text)

    stripped = final_text.strip()

    def _match_prefix(line: str) -> str:
        # Strip markdown emphasis the model sometimes wraps the marker in
        # (e.g. "**CLARIFY_NEEDED:** ...") before matching, case-insensitively.
        normalized = line.strip().strip("*_` ")
        if normalized.upper().startswith(CLARIFY_PREFIX):
            return normalized[len(CLARIFY_PREFIX):].strip(" :*_`")
        return ""

    question = _match_prefix(stripped)
    if question:
        return AgentRunResult(output=final_text, needs_clarification=True, question=question)

    # Some models append the marker as an extra line alongside a partial
    # answer instead of replying with only that line, despite instructions.
    # Scan every line: a CLARIFY_NEEDED: line anywhere means the model is
    # trying to ask something, and it must not be silently absorbed into
    # the "final" output — better to pause and ask than ship a half answer.
    for line in stripped.splitlines():
        question = _match_prefix(line)
        if question:
            return AgentRunResult(output=final_text, needs_clarification=True, question=question)

    # Fallback: some models ask their clarifying question in plain prose
    # instead of using the required prefix. A short, single-line response
    # ending in "?" is almost certainly a question, not a finished answer —
    # treat it as one rather than silently shipping it as the final output.
    lines = [line for line in stripped.splitlines() if line.strip()]
    if len(lines) == 1 and stripped.endswith("?") and len(stripped) < 300:
        return AgentRunResult(output=final_text, needs_clarification=True, question=stripped)

    return AgentRunResult(output=final_text)
