"""Authenticated run API with managed provider and background-job lifetimes."""

from contextlib import AsyncExitStack, asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request

from app.auth import router as auth_router
from app.errors import install_handlers
from app.settings import Settings
from app.llm import GeminiClient
from crustdata.client import CrustdataClient
from ledger.db import initialize
from ledger.repo import LedgerRepository
from app.document_llm import ManagedDocumentExtractor
from app.jobs import RunManager
from app.runs import router as runs_router
from ingest.claim_extractor import FallbackExtractor, extract_claims
from ingest.bharatcode_document import BharatCodeExtractor
from leo.runtime import PipelineDependencies, synthetic_dependencies
from app.uploads import UploadGuard
from agents.registry import get_agents


def create_app(
    settings: Settings | None = None, *, dependencies: PipelineDependencies | None = None
) -> FastAPI:
    # Tests inject isolated settings; production reads .env plus the environment.
    settings = settings if settings is not None else Settings()
    settings.require_auth_credentials()

    @asynccontextmanager
    async def lifespan(app):
        await initialize(settings.database_path)
        app.state.ledger = LedgerRepository(settings.database_path)
        await app.state.ledger.recover_interrupted_runs()
        app.state.crustdata = None
        app.state.llm = None
        async with AsyncExitStack() as resources:
            runtime = dependencies
            if settings.mode == "live" and runtime is None:
                settings.require_live_credentials()
                app.state.crustdata = await resources.enter_async_context(
                    CrustdataClient(settings, cache_path=settings.database_path)
                )
                app.state.llm = await resources.enter_async_context(GeminiClient(settings))
                document = await resources.enter_async_context(
                    ManagedDocumentExtractor(
                        settings.gemini_api_key.get_secret_value(),
                        timeout_s=min(60, settings.ingest_timeout),
                        attempts=1,
                    )
                )
                extractor = document
                if settings.bharatcode_api_key.get_secret_value():
                    extractor = FallbackExtractor(
                        document,
                        BharatCodeExtractor(
                            settings.bharatcode_api_key.get_secret_value(),
                            model=settings.bharatcode_model,
                            base_url=settings.bharatcode_base_url,
                        ),
                    )

                async def extract(deck):
                    return await extract_claims(deck, extractor=extractor, model=settings.model_mid)

                runtime = PipelineDependencies(
                    app.state.crustdata,
                    app.state.llm,
                    extract=extract,
                    agents=lambda: get_agents(models=(settings.model_fast, settings.model_mid)),
                )
            runtime = runtime if runtime is not None else synthetic_dependencies()
            app.state.jobs = RunManager(app.state.ledger, settings, runtime)
            try:
                yield
            finally:
                await app.state.jobs.aclose()

    app = FastAPI(
        title="Leo API",
        version="0.5.0",
        description="Authenticated upload, pipeline, status, SSE and saved reports.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    install_handlers(app)
    app.include_router(auth_router)
    app.include_router(runs_router)

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
        return {
            "status": "ok",
            "mode": settings.mode,
            "live_analysis": settings.mode == "live",
            "assessment_complete": False,
            "pipeline": True,
            "report": True,
            "limitations": [
                "Revenue figures are Crustdata estimates, and EBITDA and profit are not available from this data."
            ],
        }

    app.add_middleware(UploadGuard)
    return app
