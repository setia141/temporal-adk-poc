"""Helper for running a single-turn Google ADK agent backed by LiteLLM.

Kept separate from activities.py so the ADK/LiteLLM wiring is defined once
and reused by every agent activity.
"""

import os
from dataclasses import dataclass

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

CLARIFY_PREFIX = "CLARIFY_NEEDED:"

CLARIFY_CONVENTION = (
    "\n\nBe a diligent professional: before answering, check whether the "
    "input actually gives you what you need to do this specific task "
    "well, rather than filling gaps with convenient assumptions or "
    "placeholders. This matters a lot — a wrong guess here silently "
    "corrupts every downstream step, so treat asking as the safe default "
    "whenever you're not confident, not a last resort. Concretely: if any "
    "field you were given is a placeholder like 'TBD', 'N/A', 'unknown', "
    "'not sure', empty, or is genuinely too vague to act on, that counts "
    "as missing — you must ask about it rather than writing it through "
    "as-is or inventing a plausible-sounding value for it.\n\n"
    "If anything material is missing, vague, ambiguous, or a placeholder "
    "for YOUR task specifically, your ENTIRE response must be exactly one "
    f"line in the form '{CLARIFY_PREFIX} <your question>', with no other "
    "text alongside it. For example, given the input \"Expected "
    f"consumers: TBD\", the correct response is exactly: '{CLARIFY_PREFIX} "
    "Who are the expected consumers of this API?' — NOT a summary that "
    "carries 'TBD' through unresolved.\n\n"
    "Only if the input is genuinely complete and unambiguous for your "
    "task, respond normally with your full answer and do not ask any "
    f"questions or include a line starting with '{CLARIFY_PREFIX}' "
    "anywhere in it."
)


@dataclass
class AgentRunResult:
    output: str
    needs_clarification: bool = False
    question: str = ""


async def run_agent(
    name: str, instruction: str, prompt: str, allow_clarification: bool = True
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
        model=LiteLlm(model=model_name),
        instruction=instruction + CLARIFY_CONVENTION if allow_clarification else instruction,
    )

    app_name = "temporal-adk-poc"
    user_id = "poc-user"
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=app_name, user_id=user_id)
    runner = Runner(agent=agent, app_name=app_name, session_service=session_service)

    content = types.Content(role="user", parts=[types.Part(text=prompt)])

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
