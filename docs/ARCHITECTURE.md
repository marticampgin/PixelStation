# Architecture

Pixel Station is a local FastAPI application with a React/Vite frontend. The HTTP boundary is ready for a later desktop wrapper without depending on one.

`frontend/src` owns views and accessible controls. `backend/pixel_station` owns routes, deterministic services and provider adapters. `backend/tests` verifies rules and persistence. `config` holds non-secret integration examples. `data` holds databases, uploads, generated artifacts, images and logs and is ignored by Git.

The request workflow is route → retrieve bounded context → execute relevant tools → validate → stream a synthesized answer → persist. Plans and tool permissions are checked by application code. The inference adapter discards separate thinking fields and inline reasoning regions before public streaming or structured validation, including split and unterminated tags. Literal code and JSON-string examples remain content. External mutations require explicit confirmation of a concrete action.

Provider interfaces cover inference, embeddings, vision, images, search, web fetch, memory/vector stores, files, email/calendar, tools, games and scheduling. SQLite FTS5 provides lexical search; vector implementation is isolated. Optional integrations can be unavailable without preventing chat or local data management.

API conventions: `/api/health`, `/api/models`, `/api/settings`, `/api/conversations`, `/api/memory`, `/api/files`, `/api/web`, `/api/images`, `/api/google`, `/api/poker`, `/api/harness`. IDs are stable strings. Streaming chat uses newline-delimited JSON events with `type` and event-specific fields. Provider failures return setup and recovery information.

Complex research uses a bounded, schema-validated tool DAG. A fetch step resolves its URL from a real search result; the model cannot invent a fetch target. Simple chat skips planning. The synthesis call receives one bounded system message containing the selected evidence, which also works with small models whose templates retain only the first system message. Unsupported tool protocol is checked throughout the response and triggers at most one bounded answer retry. A streaming `reset` event clears rejected partial text in both the client and persisted response.

Embeddings use SQLite-vec through an isolated adapter, with a portable cosine fallback. Missing vectors are SQL NULL, allowing the persistent idle indexer to reindex edited content and changed embedding roles. Summaries compact ordered message batches; extraction uses only source user messages and excludes private integration results.

File edits stage and reopen a replacement, bind its exact content and original hash to an expiring single-use proposal, and retain the previous managed copy on confirmation. Google writes use the same concrete approval principle, plus version checks where available. Comfy jobs retain their submission endpoint so subsequent settings changes cannot redirect polling or cancellation.

File blobs are immutable and addressed by content hash. Generated artifacts with different requested names have independent attachment records over shared bytes; uploads retain content deduplication. Parse sidecars live under each blob's `records/<attachment-id>` directory, preserving existing legacy sidecars. Editing one record changes only its blob reference and keeps sibling records and revision bytes intact.
