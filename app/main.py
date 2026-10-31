"""Authenticated API foundation; analysis routes remain explicit placeholders."""
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request

from app.auth import get_current_user, router as auth_router
from app.errors import install_handlers
from app.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    # Tests inject isolated settings; production reads .env plus the environment.
    settings = settings if settings is not None else Settings()
    settings.require_auth_credentials()
    app = FastAPI(title="Leo API", version="0.2.0",
                  description="Demo JWT authentication is implemented. Analysis routes remain stubs.")
    app.state.settings = settings
    install_handlers(app)
    app.include_router(auth_router)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": "skeleton", "live_analysis": False}

    async def planned_route(user: str = Depends(get_current_user)):
        raise HTTPException(501, "Stage 7: run API is not implemented")

    for path, method in [("/runs", "POST"), ("/runs/{run_id}", "GET"),
                         ("/runs/{run_id}/events", "GET"), ("/runs/{run_id}/report", "GET"),
                         ("/runs/{run_id}/chat", "POST")]:
        app.add_api_route(path, planned_route, methods=[method])
    return app
