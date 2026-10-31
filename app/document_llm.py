"""Lifecycle extension of Daksh's document adapter; no ingestion code is changed."""

from ingest.gemini_document import GeminiDocumentExtractor


class ManagedDocumentExtractor(GeminiDocumentExtractor):
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._client.aio.aclose()
