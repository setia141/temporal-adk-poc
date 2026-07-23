from runner import run_agent
from shared import AgentRequest, AgentResponse

from .prompt import INSTRUCTION
from .tools import lookup_team_review_capacity


async def run_triage_classification(request: AgentRequest) -> AgentResponse:
    """Called directly from workflow code — the LLM call itself is proxied
    to a Temporal Activity by TemporalModel, so this no longer needs to be
    an @activity.defn itself."""
    result = await run_agent(
        name="triage_classification",
        instruction=INSTRUCTION,
        prompt=f"Canonical intake:\n{request.subject}\n\n{request.context}",
        tools=[lookup_team_review_capacity],
    )
    return AgentResponse(
        agent_name="triage_classification",
        output=result.output,
        needs_clarification=result.needs_clarification,
        question=result.question,
    )
