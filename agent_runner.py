"""Helper for running a single-turn Google ADK agent backed by LiteLLM.

Kept separate from activities.py so the ADK/LiteLLM wiring is defined once
and reused by every agent activity.
"""

import os

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


async def run_agent(name: str, instruction: str, prompt: str) -> str:
    """Runs one ADK agent turn and returns its final text response.

    The agent's model is a LiteLlm wrapper, which routes the request through
    LiteLLM's OpenAI-compatible client. Point it at a LiteLLM gateway by
    setting OPENAI_API_BASE; otherwise it calls OpenAI directly.
    """
    model_name = os.environ.get("ADK_MODEL", "openai/gpt-4o-mini")

    agent = LlmAgent(
        name=name,
        model=LiteLlm(model=model_name),
        instruction=instruction,
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

    return final_text
