"""Azure Blob Storage adapters: canonical document store and incoming-source reader."""

from urllib.parse import quote

from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob.aio import ContainerClient

from ..pipeline import RejectedDocument


def blob_name(document_id: str) -> str:
    """One flat, traversal-free blob name per document."""
    return quote(document_id, safe="")


class AzureBlobStore:
    """``BlobStore`` keeping the authoritative copy of each ingested document."""

    def __init__(self, container: ContainerClient) -> None:
        self._container = container

    async def put(self, document_id: str, content: bytes) -> None:
        await self._container.upload_blob(blob_name(document_id), content, overwrite=True)

    async def delete(self, document_id: str) -> None:
        try:
            await self._container.delete_blob(blob_name(document_id))
        except ResourceNotFoundError:
            return  # deletion is idempotent


class SourceTooLarge(RejectedDocument):
    """Permanent: the same blob will never fit."""


class AzureSourceReader:
    """Read an uploaded source document without ever buffering more than ``max_bytes``.

    The size is checked before download, the download is bound to the inspected ETag,
    and the stream is capped as well, so a blob replaced in between cannot slip through.
    """

    def __init__(self, container: ContainerClient, *, max_bytes: int) -> None:
        self._container = container
        self._max_bytes = max_bytes

    async def read(self, name: str) -> bytes:
        blob = self._container.get_blob_client(name)
        properties = await blob.get_blob_properties()
        if properties.size > self._max_bytes:
            raise SourceTooLarge(f"source blob exceeds {self._max_bytes} bytes")
        downloader = await blob.download_blob(
            etag=properties.etag, match_condition=MatchConditions.IfNotModified
        )
        content = bytearray()
        async for chunk in downloader.chunks():
            content.extend(chunk)
            if len(content) > self._max_bytes:
                raise SourceTooLarge(f"source blob exceeds {self._max_bytes} bytes")
        return bytes(content)
