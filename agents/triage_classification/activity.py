from temporalio import activity

from runner import run_agent
from shared import AgentRequest, AgentResponse

from .prompt import INSTRUCTION
from .tools import lookup_team_review_capacity


@activity.defn
async def triage_classification_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running triage classification agent")
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
