"""Ayush: provider gateway skeleton. No live requests are made yet."""
class CrustdataError(Exception):
    def __init__(self, kind: str, status: int | None, message: str):
        self.kind, self.status = kind, status
        super().__init__(message)


class CrustdataClient:
    async def identify(self, names=None, domains=None):
        raise NotImplementedError("Stage 4: identify with typed errors and bounded retries")

    async def enrich(self, domain=None, company_id=None, fields=None):
        raise NotImplementedError("Stage 4: enrich")

    async def job_search(self, company_id: int, title_filter=None, limit: int = 50):
        raise NotImplementedError("Stage 4: job_search")

    async def web_search(self, query: str, sources=("web", "news"), start_date=None):
        raise NotImplementedError("Stage 4: web_search")

    async def web_fetch(self, urls: tuple[str, ...]):
        raise NotImplementedError("Stage 4: web_fetch")
