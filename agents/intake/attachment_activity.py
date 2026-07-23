from dataclasses import dataclass, field

from temporalio import activity

from storage import get_attachment_store

from .attachment import Attachment, load_attachment


@dataclass
class AttachmentFetchRequest:
    refs: list[str] = field(default_factory=list)
    filenames: list[str] = field(default_factory=list)  # parallel to refs


@activity.defn
async def load_attachments_activity(request: AttachmentFetchRequest) -> list[Attachment]:
    """Real I/O (blob fetch + PDF/image parsing), unrelated to any LLM call,
    so unlike run_intake this stays a genuine Temporal Activity. One call
    loads every attachment on the form."""
    store = get_attachment_store()
    return [
        load_attachment(store.get(ref), filename)
        for ref, filename in zip(request.refs, request.filenames)
    ]
