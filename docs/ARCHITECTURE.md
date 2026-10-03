# Architecture

Pixel Station is a local FastAPI application with a React/Vite frontend. The HTTP boundary is ready for a later desktop wrapper without depending on one.

`frontend/src` owns views and accessible controls. `backend/pixel_station` owns routes, deterministic services and provider adapters. `backend/tests` verifies rules and persistence. `config` holds non-secret integration examples. `data` holds databases, uploads, generated artifacts, images and logs and is ignored by Git.

The request workflow is route → retrieve bounded context → execute relevant tools → validate → stream a synthesized answer → persist. Plans and tool permissions are checked by application code. Model thinking tokens are discarded. External mutations require explicit confirmation of a concrete action.

Provider interfaces cover inference, embeddings, vision, images, search, web fetch, memory/vector stores, files, email/calendar, tools, games and scheduling. SQLite FTS5 provides lexical search; vector implementation is isolated. Optional integrations can be unavailable without preventing chat or local data management.

API conventions: `/api/health`, `/api/models`, `/api/settings`, `/api/conversations`, `/api/memories`, `/api/files`, `/api/web`, `/api/images`, `/api/google`, `/api/poker`, `/api/harness`. IDs are stable strings. Streaming chat uses newline-delimited JSON events with `type` and event-specific fields. All errors have actionable messages.
