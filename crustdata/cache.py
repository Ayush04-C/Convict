"""Ayush: read-through cache, isolated from evidence records."""
TTLS = {"identify": 30 * 86400, "enrich": 86400, "job_search": 86400, "web_search": 21600}


class ResponseCache:
    async def get(self, endpoint: str, params: dict, api_version: str):
        raise NotImplementedError("Stage 4: TTL cache lookup")

    async def put(self, endpoint: str, params: dict, api_version: str, response: object):
        raise NotImplementedError("Stage 4: TTL cache write")
