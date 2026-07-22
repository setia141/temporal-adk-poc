from temporalio import activity

from runner import run_agent
from shared import AgentRequest, AgentResponse

from .prompt import INSTRUCTION


@activity.defn
async def architecture_evaluator_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running architecture evaluator agent")
    architecture_notes = request.context.strip() or "(none provided)"
    result = await run_agent(
        name="architecture_evaluator",
        instruction=INSTRUCTION,
        prompt=f"Canonical intake:\n{request.subject}\n\nUser-provided architecture notes:\n{architecture_notes}",
    )
    return AgentResponse(
        agent_name="architecture_evaluator",
        output=result.output,
        needs_clarification=result.needs_clarification,
        question=result.question,
    )
