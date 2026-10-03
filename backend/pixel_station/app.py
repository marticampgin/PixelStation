import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import chat, data_api, file_edits, files, google_tools, harness, memory, poker
from .config import AppSettings, data_directory
from .database import Database, Setting
from .integrations import IntegrationServices, create_integrations_router
from .providers import ModelQueue, OllamaError, OllamaProvider
from .toolset import build_tools


def create_app(data_dir: Path | None = None, llm=None, discover: bool = True) -> FastAPI:
    database = Database((data_dir or data_directory()).resolve())
    active_settings = AppSettings()

    def get_settings() -> AppSettings:
        with database.session() as session:
            row = session.get(Setting, "application")
            return AppSettings.model_validate(row.value) if row else active_settings

    def set_settings(settings: AppSettings) -> None:
        with database.session() as session:
            row = session.get(Setting, "application")
            if row and any(
                row.value.get("roles", {}).get(role, "") != settings.roles[role]
                for role in ("primary_chat", "summarizer", "memory_extractor")
            ):
                from sqlalchemy import update

                from .database import ScheduledJob, now

                session.execute(
                    update(ScheduledJob)
                    .where(ScheduledJob.id.like("summary:%"))
                    .values(next_run=now())
                )
            if (
                row
                and row.value.get("roles", {}).get("embedding", "") != settings.roles["embedding"]
            ):
                from sqlalchemy import update

                from .database import DocumentChunk, Memory, ScheduledJob, now

                session.execute(update(Memory).values(embedding=None))
                session.execute(update(DocumentChunk).values(embedding=None))
                job = session.get(ScheduledJob, "vectors")
                if job:
                    job.next_run = now()
            if row:
                row.value = settings.model_dump()
            else:
                session.add(Setting(key="application", value=settings.model_dump()))
            session.commit()

    def get_setting(key: str, default=None):
        settings = get_settings()
        if hasattr(settings, key):
            return getattr(settings, key)
        with database.session() as session:
            row = session.get(Setting, key)
            return row.value.get("value", default) if row else default

    def set_setting(key: str, value) -> None:
        settings = get_settings()
        if key in AppSettings.model_fields:
            set_settings(AppSettings.model_validate({**settings.model_dump(), key: value}))
            return
        with database.session() as session:
            row = session.get(Setting, key)
            if row:
                row.value = {"value": value}
            else:
                session.add(Setting(key=key, value={"value": value}))
            session.commit()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.migrate()
        # Requests interrupted by a restart remain inspectable instead of stuck as generating.
        from sqlalchemy import select

        from .database import AgentRun, Message, now

        with database.session() as session:
            for message in session.scalars(select(Message).where(Message.status == "generating")):
                message.status = "interrupted"
            for run in session.scalars(select(AgentRun).where(AgentRun.status == "running")):
                run.status, run.finished_at = "interrupted", now()
            session.commit()
        if discover:
            discovery = await app.state.llm.models()
            candidates = [
                model
                for model in discovery.get("models", [])
                if "completion" in model.get("capabilities", [])
            ]
            if candidates and not get_settings().roles["primary_chat"]:
                settings = get_settings()
                for role in (
                    "primary_chat",
                    "planner",
                    "router",
                    "summarizer",
                    "memory_extractor",
                    "critic",
                ):
                    settings.roles[role] = candidates[0]["name"]
                set_settings(settings)
        scheduler = harness.PersistentScheduler(app)
        scheduler_task = asyncio.create_task(scheduler.loop())
        try:
            yield
        finally:
            scheduler_task.cancel()
            for task in app.state.background_tasks:
                task.cancel()
            await asyncio.gather(
                scheduler_task, *app.state.background_tasks, return_exceptions=True
            )
            await app.state.integration_services.close()
            database.engine.dispose()

    app = FastAPI(title="Pixel Station", lifespan=lifespan)

    @app.exception_handler(OllamaError)
    async def local_model_error(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(httpx.HTTPError)
    async def provider_http_error(_request, exc):
        return JSONResponse(
            {"detail": f"Provider request failed: {str(exc)[:500]}"}, status_code=503
        )

    @app.exception_handler(TimeoutError)
    async def provider_timeout(_request, _exc):
        return JSONResponse(
            {
                "detail": "The local provider exceeded its execution time limit. Check the service and retry."
            },
            status_code=504,
        )

    app.state.database = database
    app.state.data_dir = (data_dir or data_directory()).resolve()
    app.state.settings, app.state.set_settings = get_settings, set_settings
    app.state.llm = llm or OllamaProvider(get_settings)
    app.state.model_queue = ModelQueue()
    app.state.active_generations = {}
    app.state.generation_tasks = {}
    app.state.background_tasks = set()
    app.state.compaction_locks = {}
    # Migrate before optional providers read settings; repeated Alembic upgrades are idempotent.
    database.migrate()
    app.state.integration_services = IntegrationServices(
        app.state.data_dir, get_setting, set_setting
    )
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
            hostname = urlsplit(origin).hostname
            if hostname not in {"127.0.0.1", "localhost", "::1"}:
                return JSONResponse(
                    {"detail": "Only local browser origins may change Pixel Station data"},
                    status_code=403,
                )
        return await call_next(request)

    for router in (
        chat.router,
        memory.router,
        files.router,
        file_edits.create_file_edit_router(app),
        harness.router,
        data_api.router,
        google_tools.router,
        poker.create_poker_router(),
        create_integrations_router(app.state.integration_services),
    ):
        app.include_router(router)

    @app.get("/api/health")
    async def health():
        discovery = await app.state.llm.models()
        return {
            "status": "ok",
            "ollama": discovery["available"],
            "data_dir": str(app.state.data_dir),
            "version": "0.1.0",
        }

    @app.get("/api/models")
    async def models():
        return await app.state.llm.models()

    @app.get("/api/models/capabilities")
    async def capabilities(name: str):
        discovery = await app.state.llm.models()
        for model in discovery["models"]:
            if model["name"] == name:
                return model
        raise HTTPException(404, f"Local model not installed. Run ollama pull {name}")

    @app.get("/api/settings")
    def settings():
        return get_settings().model_dump()

    @app.put("/api/settings")
    def update_settings(payload: AppSettings):
        set_settings(payload)
        app.state.llm._cache.clear() if hasattr(app.state.llm, "_cache") else None
        return payload.model_dump()

    registry = build_tools(app)
    app.state.tool_registry = registry

    @app.get("/api/tools")
    def tools(intent: str = ""):
        relevant = (
            {
                "normal_chat": [],
                "web_research": ["web_search", "web_fetch"],
                "file_question": ["file_retrieve"],
                "file_summary": ["file_retrieve"],
            }.get(intent, [intent])
            if intent
            else list(registry.tools)
        )
        return [registry.tools[key].metadata() for key in relevant if key in registry.tools]

    return app


app = create_app()
