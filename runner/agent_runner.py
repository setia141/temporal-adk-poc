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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types
from temporalio import workflow
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
    images: list[tuple[bytes, str]] | None = None,
    tools: list[Callable] | None = None,
) -> AgentRunResult:
    """Runs one ADK agent turn and returns its final text response.

    The agent's model is `TemporalModel`, which resolves `ADK_MODEL` through
    ADK's `LLMRegistry` — an `openai/...`-prefixed name resolves to a
    `LiteLlm`-backed call (see runner/gateway_litellm.py for custom gateway
    base-URL/header injection); the call itself happens inside Temporal's
    own `invoke_model` Activity.

    When allow_clarification is False, the agent is never allowed to pause
    the workflow to ask the user something — used for stages that run in
    parallel with another stage, since the workflow's clarification state
    only supports one pending question at a time.

    tools are the workflow-side wrapper functions from agents/<name>/tools.py:
    ADK wraps each in a FunctionTool and drives the tool-calling loop here in
    workflow code, and each wrapper immediately dispatches its real work to
    its own Temporal activity — never do real I/O directly in a tool passed
    here, since this function runs as workflow code.
    """
    model_name = os.environ.get("ADK_MODEL", "openai/gpt-4o-mini")
    # workflow.logger is replay-safe (suppressed during replay), unlike a
    # plain module logger, which would double-log every recorded call.
    workflow.logger.info(
        "LLM call: agent=%s model=%s prompt_chars=%d images=%d tools=%s",
        name, model_name, len(prompt), len(images or []),
        [t.__name__ for t in tools or []],
    )

    agent = LlmAgent(
        name=name,
        model=TemporalModel(
            model_name,
            activity_config=ActivityConfig(**DEFAULT_ACTIVITY_CONFIG, summary=name),
        ),
        instruction=instruction + CLARIFY_CONVENTION if allow_clarification else instruction,
        tools=tools or [],
    )

    app_name = "temporal-adk-poc"
    user_id = "poc-user"
    runner = InMemoryRunner(agent=agent, app_name=app_name)
    session = await runner.session_service.create_session(app_name=app_name, user_id=user_id)

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

    workflow.logger.info(
        "LLM response: agent=%s response_chars=%d preview=%r",
        name, len(final_text), final_text[:200],
    )

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
