from runner import run_agent
from shared import AgentRequest, AgentResponse

from .attachment import Attachment
from .prompt import INSTRUCTION
from .tools import lookup_requesting_team


async def run_intake(request: AgentRequest, attachments: list[Attachment]) -> AgentResponse:
    """Called directly from workflow code — the LLM call itself is proxied
    to a Temporal Activity by TemporalModel. `attachments` are fetched and
    parsed separately (see attachment_activity.py), since that's genuine I/O
    unrelated to the LLM call itself and stays a real Activity."""
    text_sections = []
    images: list[tuple[bytes, str]] = []
    for i, attachment in enumerate(attachments):
        if attachment.text:
            text_sections.append(f"\n\nSupporting attachment #{i + 1}:\n{attachment.text}")
        elif attachment.image_bytes:
            text_sections.append(f"\n\n(Supporting image #{i + 1} attached below.)")
            images.append((attachment.image_bytes, attachment.image_mime_type))
    attachment_section = "".join(text_sections)

    result = await run_agent(
        name="intake_preparation",
        instruction=INSTRUCTION,
        prompt=f"Raw intake form:\n{request.subject}\n\n{request.context}{attachment_section}".strip(),
        images=images,
        tools=[lookup_requesting_team],
    )
    return AgentResponse(
        agent_name="intake_preparation",
        output=result.output,
        needs_clarification=result.needs_clarification,
        question=result.question,
    )
