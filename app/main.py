"""Skeleton API only. Planned routes return 501 until their owning stage."""
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request

from app.auth import get_current_user
from app.errors import install_handlers
from app.settings import Settings


def create_app() -> FastAPI:
    app = FastAPI(title="Leo API skeleton", version="0.1.0",
                  description="Stub foundation. Login, analysis and reports are not implemented.")
    app.state.settings = Settings()
    install_handlers(app)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": "skeleton", "live_analysis": False}

    @app.post("/auth/login")
    async def login():
        raise HTTPException(501, "Stage 2: demo login is not implemented")

    async def planned_route(user: str = Depends(get_current_user)):
        raise HTTPException(501, "Stage 7: run API is not implemented")

    for path, method in [("/runs", "POST"), ("/runs/{run_id}", "GET"),
                         ("/runs/{run_id}/events", "GET"), ("/runs/{run_id}/report", "GET"),
                         ("/runs/{run_id}/chat", "POST")]:
        app.add_api_route(path, planned_route, methods=[method])
    return app
