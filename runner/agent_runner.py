"""Helper for running a single-turn Google ADK agent whose model calls are
proxied through Temporal Activities via the official
`temporalio.contrib.google_adk_agents` plugin (`TemporalModel` +
`GoogleAdkPlugin`, configured on the Worker's Client in worker.py).

Unlike the hand-rolled version this replaces, `run_agent` is meant to be
awaited directly from workflow code, not wrapped in its own `@activity.defn`:
`TemporalModel.generate_content_async` is what turns the actual LLM call
into a Temporal Activity (`invoke_model`) under the hood, so everything
around it (Agent construction, Runner event loop, response parsing) is safe
to run in workflow code as long as GoogleAdkPlugin is configured on the
Worker (it adds `google.adk`/`google.genai`/`mcp` to the sandbox passthrough
and patches ADK's time/uuid providers to Temporal's deterministic ones).
"""

import os
from dataclasses import dataclass
from datetime import timedelta

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types
from temporalio.common import RetryPolicy
from temporalio.contrib.google_adk_agents import TemporalModel
from temporalio.workflow import ActivityConfig

from .clarify_prompt import CLARIFY_CONVENTION, CLARIFY_PREFIX

DEFAULT_ACTIVITY_CONFIG = ActivityConfig(
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy=RetryPolicy(
        initial_interval=timedelta(seconds=2),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=3,
    ),
)


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
    image_bytes: bytes | None = None,
    image_mime_type: str = "",
) -> AgentRunResult:
    """Runs one ADK agent turn and returns its final text response.

    The agent's model is `TemporalModel`, which resolves `ADK_MODEL` through
    ADK's `LLMRegistry` — an `openai/...`-prefixed name still resolves to a
    `LiteLlm`-backed call under the hood, so `OPENAI_API_BASE`/`OPENAI_API_KEY`
    (e.g. pointed at an org LiteLLM gateway) work exactly as before; the only
    difference is that call now happens inside Temporal's own `invoke_model`
    Activity instead of one we wrote ourselves.

    When allow_clarification is False, the agent is never allowed to pause
    the workflow to ask the user something — used for activities that run
    in parallel with another activity, since the workflow's clarification
    state only supports one pending question at a time.
    """
    model_name = os.environ.get("ADK_MODEL", "openai/gpt-4o-mini")

    agent = LlmAgent(
        name=name,
        model=TemporalModel(
            model_name,
            activity_config=ActivityConfig(**DEFAULT_ACTIVITY_CONFIG, summary=name),
        ),
        instruction=instruction + CLARIFY_CONVENTION if allow_clarification else instruction,
    )

    app_name = "temporal-adk-poc"
    user_id = "poc-user"
    runner = InMemoryRunner(agent=agent, app_name=app_name)
    session = await runner.session_service.create_session(app_name=app_name, user_id=user_id)

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
