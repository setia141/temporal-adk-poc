from runner import run_agent
from shared import AgentRequest, AgentResponse

from .prompt import INSTRUCTION
from .tools import lookup_downstream_dependencies


async def run_complexity_assessment(request: AgentRequest) -> AgentResponse:
    """Called directly from workflow code — the LLM call itself is proxied
    to a Temporal Activity by TemporalModel, so this no longer needs to be
    an @activity.defn itself."""
    result = await run_agent(
        name="complexity_assessment",
        instruction=INSTRUCTION,
        prompt=f"Canonical intake:\n{request.subject}",
        allow_clarification=False,
        tools=[lookup_downstream_dependencies],
    )
    return AgentResponse(agent_name="complexity_assessment", output=result.output)
