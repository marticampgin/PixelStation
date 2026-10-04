"""Concrete tools share schemas and provider implementations with direct workspaces."""

from pydantic import BaseModel, Field

from .database import record_dict
from .files import FileCreate, retrieve_files, write_generated
from .indexing import embed_query
from .integrations import EmailInput, EventInput
from .memory import MemoryInput, create_memory, search_memory
from .orchestration import Tool, ToolRegistry
from .providers.comfy import MEMORY_RELEASE_TIMEOUT, REMOTE_CLEANUP_TIMEOUT


class Query(BaseModel):
    query: str = ""


class URL(BaseModel):
    url: str


class ImagePrompt(BaseModel):
    prompt: str = Field(min_length=1, max_length=10000)


class FileQuery(BaseModel):
    attachment_ids: list[str] = Field(max_length=10)
    query: str


class ThreadQuery(BaseModel):
    thread_id: str


class AgendaQuery(BaseModel):
    time_min: str
    time_max: str
    calendar_id: str = "primary"
    query: str = ""


class CalendarUpdate(EventInput):
    event_id: str


class CalendarDelete(BaseModel):
    calendar_id: str = "primary"
    event_id: str


def build_tools(app) -> ToolRegistry:
    registry = ToolRegistry()
    services = app.state.integration_services

    def register(identifier, category, schema_class, execute, permission="read_only", timeout=45):
        schema = schema_class.model_json_schema()
        schema["additionalProperties"] = False
        registry.register(
            Tool(
                identifier,
                identifier.replace("_", " ").title(),
                identifier.replace("_", " "),
                category,
                schema,
                execute,
                permission=permission,
                timeout=timeout,
                required_integration=category if category in {"web", "images", "google"} else None,
            )
        )

    async def query_memories(query=""):
        vector = await embed_query(app, query) if query else None
        with app.state.database.session() as session:
            return search_memory(
                session, query, app.state.settings().retrieval_count, embedding=vector
            )

    async def save_memory(**kwargs):
        with app.state.database.session() as session:
            return record_dict(create_memory(session, MemoryInput.model_validate(kwargs)))

    async def query_file(attachment_ids, query):
        vector = await embed_query(app, query)
        with app.state.database.session() as session:
            return retrieve_files(session, attachment_ids, query, embedding=vector)

    async def create_file(**kwargs):
        import asyncio

        def write():
            with app.state.database.session() as session:
                return record_dict(
                    write_generated(session, app.state.data_dir, FileCreate.model_validate(kwargs))
                )

        return await asyncio.to_thread(write)

    async def read_calendar(time_min, time_max, calendar_id="primary", query=""):
        return await services.google.events(calendar_id, time_min, time_max, query)

    async def update_calendar(calendar_id, event, event_id):
        return await services.propose_action("calendar_update", {
            "calendar_id": calendar_id, "event_id": event_id, "event": event,
        })

    async def create_calendar(calendar_id, event):
        return await services.propose_action("calendar_create", {
            "calendar_id": calendar_id, "event": event,
        })

    async def delete_calendar(calendar_id, event_id):
        return await services.propose_action("calendar_delete", {
            "calendar_id": calendar_id, "event_id": event_id,
        })

    async def create_draft(**kwargs):
        return await services.create_gmail_draft(EmailInput.model_validate(kwargs).model_dump())

    async def propose_send(**kwargs):
        # Registry permission approval is not a concrete account/payload-bound
        # send review. Only the dedicated confirmation service performs a send.
        return await services.propose_action(
            "gmail_send", EmailInput.model_validate(kwargs).model_dump()
        )

    async def read_thread(thread_id):
        return await services.google.thread(thread_id)

    register("web_search", "web", Query, services.web.search)
    register("web_fetch", "web", URL, services.web.fetch)
    register(
        "image_generate", "images", ImagePrompt, services.images.generate, "local_reversible",
        services.images.timeout + REMOTE_CLEANUP_TIMEOUT + MEMORY_RELEASE_TIMEOUT,
    )
    register("memory_query", "memory", Query, query_memories)
    register("memory_write", "memory", MemoryInput, save_memory, "local_reversible")
    register("file_retrieve", "files", FileQuery, query_file)
    register("file_create", "files", FileCreate, create_file, "local_reversible", 120)
    register("gmail_search", "google", Query, services.google.threads)
    register("gmail_read", "google", ThreadQuery, read_thread)
    register("gmail_draft", "google", EmailInput, create_draft, "local_reversible")
    register(
        "gmail_send", "google", EmailInput, propose_send, "external_or_destructive"
    )
    register("calendar_read", "google", AgendaQuery, read_calendar)
    register("calendar_create", "google", EventInput, create_calendar, "external_or_destructive")
    register(
        "calendar_update", "google", CalendarUpdate, update_calendar, "external_or_destructive"
    )
    register(
        "calendar_delete", "google", CalendarDelete, delete_calendar, "external_or_destructive"
    )
    return registry
