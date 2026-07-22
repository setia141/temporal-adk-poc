import logging

from runner import run_agent
from shared import AgentRequest, AgentResponse

from .prompt import INSTRUCTION

logger = logging.getLogger(__name__)


async def run_architecture_evaluator(request: AgentRequest) -> AgentResponse:
    """Called directly from workflow code — the LLM call itself is proxied
    to a Temporal Activity by TemporalModel, so this no longer needs to be
    an @activity.defn itself."""
    logger.info("Running architecture evaluator agent")
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
