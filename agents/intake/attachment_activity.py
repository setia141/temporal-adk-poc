from dataclasses import dataclass

from temporalio import activity

from storage import get_attachment_store

from .attachment import Attachment, load_attachment


@dataclass
class AttachmentFetchRequest:
    ref: str
    filename: str


@activity.defn
async def load_attachment_activity(request: AttachmentFetchRequest) -> Attachment:
    """Real I/O (blob fetch + PDF/image parsing), unrelated to any LLM call,
    so unlike run_intake this stays a genuine Temporal Activity."""
    data = get_attachment_store().get(request.ref)
    return load_attachment(data, request.filename)
