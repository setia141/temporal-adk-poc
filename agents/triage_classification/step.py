import logging

from runner import run_agent
from shared import AgentRequest, AgentResponse

from .prompt import INSTRUCTION

logger = logging.getLogger(__name__)


async def run_triage_classification(request: AgentRequest) -> AgentResponse:
    """Called directly from workflow code — the LLM call itself is proxied
    to a Temporal Activity by TemporalModel, so this no longer needs to be
    an @activity.defn itself."""
    logger.info("Running triage classification agent")
    result = await run_agent(
        name="triage_classification",
        instruction=INSTRUCTION,
        prompt=f"Canonical intake:\n{request.subject}\n\n{request.context}",
    )
    return AgentResponse(
        agent_name="triage_classification",
        output=result.output,
        needs_clarification=result.needs_clarification,
        question=result.question,
    )
