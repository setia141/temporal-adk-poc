import logging

from runner import run_agent
from shared import AgentRequest, AgentResponse

from .prompt import INSTRUCTION

logger = logging.getLogger(__name__)


async def run_risk_scoring(request: AgentRequest) -> AgentResponse:
    """Called directly from workflow code — the LLM call itself is proxied
    to a Temporal Activity by TemporalModel, so this no longer needs to be
    an @activity.defn itself."""
    logger.info("Running risk scoring agent")
    result = await run_agent(
        name="risk_scoring",
        instruction=INSTRUCTION,
        prompt=f"Canonical intake:\n{request.subject}",
        allow_clarification=False,
    )
    return AgentResponse(agent_name="risk_scoring", output=result.output)
