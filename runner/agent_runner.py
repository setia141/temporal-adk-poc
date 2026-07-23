"""Helper for running a single-turn Google ADK agent backed by LiteLLM.

Kept separate from the agent activities so the ADK/LiteLLM wiring is
defined once and reused by every agent activity.
"""

import os
from collections.abc import Callable
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


async def run_agent(
    name: str,
    instruction: str,
    prompt: str,
    allow_clarification: bool = True,
    images: list[tuple[bytes, str]] | None = None,
    tools: list[Callable] | None = None,
) -> AgentRunResult:
    """Runs one ADK agent turn and returns its final text response.

    The agent's model is a LiteLlm wrapper, which routes the request through
    LiteLLM's OpenAI-compatible client. Point it at a LiteLLM gateway by
    setting OPENAI_API_BASE; otherwise it calls OpenAI directly.

    When allow_clarification is False, the agent is never allowed to pause
    the workflow to ask the user something — used for activities that run
    in parallel with another activity, since the workflow's clarification
    state only supports one pending question at a time.

    tools are plain async functions; ADK wraps each in a FunctionTool and
    drives any tool-calling loop itself inside runner.run_async below. Since
    run_agent is only ever awaited from inside an @activity.defn (never from
    workflow code), a tool that does real I/O is safe here the same way the
    LLM call itself is — no Temporal determinism/sandbox concerns apply.
    """
    model_name = os.environ.get("ADK_MODEL", "openai/gpt-4o-mini")

    agent = LlmAgent(
        name=name,
        model=LiteLlm(model=model_name),
        instruction=instruction + CLARIFY_CONVENTION if allow_clarification else instruction,
        tools=tools or [],
    )

    app_name = "temporal-adk-poc"
    user_id = "poc-user"
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=app_name, user_id=user_id)
    runner = Runner(agent=agent, app_name=app_name, session_service=session_service)

    parts = [types.Part(text=prompt)]
    for image_bytes, image_mime_type in images or []:
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

    # A CLARIFY_NEEDED: line anywhere in the response means the model is
    # trying to ask something (covers both the simple "entire response is
    # just that line" case and a model that explains something — e.g.
    # answering a follow-up question from the user — before re-asking).
    # display keeps any such explanatory text but replaces the raw marker
    # line with the bare question, so the user sees the full conversational
    # reply instead of just the extracted question.
    lines = stripped.splitlines()
    for i, line in enumerate(lines):
        question = _match_prefix(line)
        if question:
            display = "\n".join([*lines[:i], question, *lines[i + 1 :]]).strip()
            return AgentRunResult(output=display, needs_clarification=True, question=question)

    # Fallback: some models ask their clarifying question in plain prose
    # instead of using the required prefix. A short, single-line response
    # ending in "?" is almost certainly a question, not a finished answer —
    # treat it as one rather than silently shipping it as the final output.
    non_blank_lines = [line for line in lines if line.strip()]
    if len(non_blank_lines) == 1 and stripped.endswith("?") and len(stripped) < 300:
        return AgentRunResult(output=stripped, needs_clarification=True, question=stripped)

    return AgentRunResult(output=final_text)
