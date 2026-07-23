from temporalio import activity

from runner import run_agent
from shared import AgentRequest, AgentResponse

from .prompt import INSTRUCTION
from .tools import lookup_prior_incidents


@activity.defn
async def risk_scoring_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running risk scoring agent")
    result = await run_agent(
        name="risk_scoring",
        instruction=INSTRUCTION,
        prompt=f"Canonical intake:\n{request.subject}",
        allow_clarification=False,
        tools=[lookup_prior_incidents],
    )
    return AgentResponse(agent_name="risk_scoring", output=result.output)
