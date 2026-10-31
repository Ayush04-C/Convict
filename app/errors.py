"""Ayush: safe shared error format; never echo raw request data."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger("leo.api")


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(status, message)
        self.code = code


def install_handlers(app: FastAPI) -> None:
    def response(request, status, code, message, headers=None):
        rid = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=status,
            content={"error": {"code": code, "message": message, "request_id": rid}},
            headers={**(headers or {}), "X-Request-ID": rid},
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = getattr(
            exc, "code", "UNAUTHORIZED" if exc.status_code == 401 else f"HTTP_{exc.status_code}"
        )
        return response(request, exc.status_code, code, str(exc.detail), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return response(request, 422, "VALIDATION_ERROR", "Request data is invalid.")

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        # Exception text/tracebacks can contain provider secrets or user input.
        logger.error(
            "Unhandled %s request_id=%s",
            type(exc).__name__,
            getattr(request.state, "request_id", "unknown"),
        )
        return response(request, 500, "INTERNAL_ERROR", "An unexpected error occurred.")
