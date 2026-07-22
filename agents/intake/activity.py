from temporalio import activity

from runner import run_agent
from shared import AgentRequest, AgentResponse
from storage import get_attachment_store

from .attachment import Attachment, load_attachment
from .prompt import INSTRUCTION


@activity.defn
async def intake_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running intake preparation agent")
    attachment_section = ""
    attachment = Attachment()
    if request.attachment_ref:
        data = get_attachment_store().get(request.attachment_ref)
        attachment = load_attachment(data, request.attachment_filename)
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
