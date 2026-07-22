import logging

from runner import run_agent
from shared import AgentRequest, AgentResponse

from .attachment import Attachment
from .prompt import INSTRUCTION

logger = logging.getLogger(__name__)


async def run_intake(request: AgentRequest, attachment: Attachment) -> AgentResponse:
    """Called directly from workflow code — the LLM call itself is proxied
    to a Temporal Activity by TemporalModel. `attachment` is fetched and
    parsed separately (see attachment_activity.py), since that's genuine I/O
    unrelated to the LLM call itself and stays a real Activity."""
    logger.info("Running intake preparation agent")
    attachment_section = ""
    if attachment.text:
        attachment_section = f"\n\nSupporting attachment text:\n{attachment.text}"
    elif attachment.image_bytes:
        attachment_section = "\n\n(A supporting image is attached below.)"

    result = await run_agent(
        name="intake_preparation",
        instruction=INSTRUCTION,
        prompt=f"Raw intake form:\n{request.subject}\n\n{request.context}{attachment_section}".strip(),
        image_bytes=attachment.image_bytes,
        image_mime_type=attachment.image_mime_type,
    )
    return AgentResponse(
        agent_name="intake_preparation",
        output=result.output,
        needs_clarification=result.needs_clarification,
        question=result.question,
    )
