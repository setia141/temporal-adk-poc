from temporalio import activity

from runner import run_agent
from shared import AgentRequest, AgentResponse

from .prompt import INSTRUCTION
from .tools import lookup_downstream_dependencies


@activity.defn
async def complexity_assessment_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running complexity assessment agent")
    result = await run_agent(
        name="complexity_assessment",
        instruction=INSTRUCTION,
        prompt=f"Canonical intake:\n{request.subject}",
        allow_clarification=False,
        tools=[lookup_downstream_dependencies],
    )
    return AgentResponse(agent_name="complexity_assessment", output=result.output)
