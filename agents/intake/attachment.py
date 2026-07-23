"""Parses a user-supplied attachment (PDF, image, or text/markdown) into
either extractable text or base64-encoded image data, for the intake step to
fold into its prompt to the LLM."""

import base64
import io
import mimetypes
from dataclasses import dataclass

from pypdf import PdfReader
from temporalio import activity

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".log"}


@dataclass
class Attachment:
    text: str = ""  # extracted/read text to fold into the prompt
    # Images are carried base64-encoded, not as raw bytes: this dataclass is
    # an activity return value on this branch, and the plugin's pydantic JSON
    # payload converter cannot serialize non-UTF-8 raw bytes.
    image_b64: str = ""
    image_mime_type: str = ""


def load_attachment(data: bytes, filename: str) -> Attachment:
    """Best-effort parse of a user-supplied attachment, given as bytes the
    client already read and sent through the workflow — no assumption that
    client and worker share a filesystem. User-supplied files can be corrupt
    or an unsupported type, so failures degrade to a note for the agent
    rather than failing the activity."""
    ext = ("." + filename.lower().rsplit(".", 1)[-1]) if "." in filename else ""
    try:
        if ext == ".pdf":
            pages = PdfReader(io.BytesIO(data)).pages
            text = "\n".join(page.extract_text() or "" for page in pages).strip()
            return Attachment(text=text or "(PDF contained no extractable text)")
        if ext in TEXT_EXTENSIONS:
            return Attachment(text=data.decode("utf-8"))
        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type and mime_type.startswith("image/"):
            return Attachment(
                image_b64=base64.b64encode(data).decode("ascii"),
                image_mime_type=mime_type,
            )
        return Attachment(text=f"(Unsupported attachment type: {filename})")
    except Exception as exc:
        activity.logger.warning("Attachment parse failed for %s: %s", filename, exc)
        return Attachment(text=f"(Could not read attachment {filename}: {exc})")
