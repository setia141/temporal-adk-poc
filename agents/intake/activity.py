from temporalio import activity

from runner import run_agent
from shared import AgentRequest, AgentResponse
from storage import get_attachment_store

from .attachment import load_attachment
from .prompt import INSTRUCTION
from .tools import lookup_requesting_team


@activity.defn
async def intake_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running intake preparation agent")
    store = get_attachment_store()
    text_sections = []
    images: list[tuple[bytes, str]] = []
    for ref, filename in zip(request.attachment_refs, request.attachment_filenames):
        attachment = load_attachment(store.get(ref), filename)
        if attachment.text:
            text_sections.append(f"\n\nSupporting attachment ({filename}):\n{attachment.text}")
        elif attachment.image_bytes:
            text_sections.append(f"\n\n(Supporting image attached below: {filename})")
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
