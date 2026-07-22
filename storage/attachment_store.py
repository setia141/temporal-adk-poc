"""Pluggable storage for user-supplied intake attachments.

The workflow and its activities never see a filesystem path — only a small
string "ref" that a store can turn back into bytes. That's what makes this
resilient to the client and worker running on different hosts/containers:
Temporal already guarantees both sides can reach the Temporal server, so as
long as the ref (not the file) travels through Temporal, storage location is
irrelevant to that concern. Swapping backends is an env var, not a code change.

- "inline" (default): no external storage at all. The ref *is* the
  base64-encoded data, so it rides through Temporal's own workflow/activity
  payloads. Zero infra, but bounded by Temporal's payload/history size limits
  (fine for typical KB-to-few-MB attachments).
- "azure_blob": persists to Azure Blob Storage; the ref is just a blob name.
  Removes the size ceiling, at the cost of a real external dependency.
"""

import base64
import os
import uuid


class AttachmentStore:
    def put(self, data: bytes, filename: str) -> str:
        raise NotImplementedError

    def get(self, ref: str) -> bytes:
        raise NotImplementedError


class InlineAttachmentStore(AttachmentStore):
    def put(self, data: bytes, filename: str) -> str:
        return base64.b64encode(data).decode("ascii")

    def get(self, ref: str) -> bytes:
        return base64.b64decode(ref)


class AzureBlobAttachmentStore(AttachmentStore):
    """Requires AZURE_STORAGE_CONNECTION_STRING; container name is
    configurable via AZURE_STORAGE_CONTAINER (default: intake-attachments)."""

    def __init__(self) -> None:
        from azure.storage.blob import BlobServiceClient

        conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        container_name = os.environ.get("AZURE_STORAGE_CONTAINER", "intake-attachments")
        client = BlobServiceClient.from_connection_string(conn_str)
        self._container = client.get_container_client(container_name)
        if not self._container.exists():
            self._container.create_container()

    def put(self, data: bytes, filename: str) -> str:
        blob_name = f"{uuid.uuid4()}-{filename}"
        self._container.upload_blob(blob_name, data)
        return blob_name

    def get(self, ref: str) -> bytes:
        return self._container.download_blob(ref).readall()


def get_attachment_store() -> AttachmentStore:
    backend = os.environ.get("ATTACHMENT_STORE", "inline").lower()
    if backend == "azure_blob":
        return AzureBlobAttachmentStore()
    return InlineAttachmentStore()
