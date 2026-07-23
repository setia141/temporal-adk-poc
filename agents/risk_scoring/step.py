from runner import run_agent
from shared import AgentRequest, AgentResponse

from .prompt import INSTRUCTION
from .tools import lookup_prior_incidents


async def run_risk_scoring(request: AgentRequest) -> AgentResponse:
    """Called directly from workflow code — the LLM call itself is proxied
    to a Temporal Activity by TemporalModel, so this no longer needs to be
    an @activity.defn itself."""
    result = await run_agent(
        name="risk_scoring",
        instruction=INSTRUCTION,
        prompt=f"Canonical intake:\n{request.subject}",
        allow_clarification=False,
        tools=[lookup_prior_incidents],
    )
    return AgentResponse(agent_name="risk_scoring", output=result.output)
