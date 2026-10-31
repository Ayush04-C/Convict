"""Bound request bytes before multipart parsing; file payload is checked separately."""

import asyncio
import tempfile
from uuid import uuid4

from fastapi import Request
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.auth import bearer, get_current_user

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 64 * 1024


class UploadGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/runs":
            return await self.app(scope, receive, send)
        request = Request(scope)
        rid = str(uuid4())

        async def reject(status, code, message, headers=None):
            response = JSONResponse(
                {"error": {"code": code, "message": message, "request_id": rid}},
                status_code=status,
                headers={"X-Request-ID": rid, **(headers or {})},
            )
            await response(scope, receive, send)

        try:
            get_current_user(request, await bearer(request))
        except HTTPException as error:
            return await reject(error.status_code, "UNAUTHORIZED", str(error.detail), error.headers)
        length = request.headers.get("content-length")
        if length is not None:
            try:
                size = int(length)
                if size < 0:
                    raise ValueError
            except ValueError:
                return await reject(400, "INVALID_LENGTH", "Invalid request length.")
            if size > MAX_REQUEST_BYTES:
                return await reject(413, "DECK_TOO_LARGE", "Upload request exceeds the size limit.")
        # Bound chunked bodies too. Spool to disk after 1 MB, without trusting headers.
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as body:
            total = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > MAX_REQUEST_BYTES:
                    return await reject(
                        413, "DECK_TOO_LARGE", "Upload request exceeds the size limit."
                    )
                await asyncio.to_thread(body.write, chunk)
                if not message.get("more_body", False):
                    break
            body.seek(0)
            finished = False

            async def replay():
                nonlocal finished
                if finished:
                    return await receive()
                chunk = await asyncio.to_thread(body.read, 128 * 1024)
                finished = not chunk
                return {"type": "http.request", "body": chunk, "more_body": bool(chunk)}

            await self.app(scope, replay, send)
