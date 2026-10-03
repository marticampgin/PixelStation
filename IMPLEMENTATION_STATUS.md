# Pixel Station implementation status

## Completed

- Inspected the initial empty repository and Windows environment; verified NVIDIA driver 617.14, RTX 3060 Laptop GPU (6 GB VRAM) and 8 GB RAM.
- Installed isolated Python 3.12, hashed backend/document dependencies, frontend dependencies, Ollama and local chat/embedding models. System Python remains unchanged.
- Built the modular FastAPI/React application with loopback binding, SQLite WAL/FTS5, static Alembic migrations and sqlite-vec with a portable cosine fallback.
- Persistent streaming chat, installed-model selection, cancellation, regeneration, feedback, searchable/archivable conversations and bounded task/context/response execution.
- Explicit and automatically extracted memories, pinning, revisions, provenance, links, lexical/vector retrieval and persistent conversation compaction/indexing jobs.
- Paperclip attachments, document parsing/chunk retrieval, deterministic TXT/MD/CSV/XLSX/DOCX/PDF generation and exact, expiring, single-use edit confirmation with preserved revisions.
- Actual SearXNG/public-web research and ComfyUI workflow/job/library providers, including bounded research DAGs, SSRF-safe fetching, cancellation and retained cleanup outcomes.
- Official Google OAuth/Gmail/Calendar providers, typed read/write operations, immutable confirmations for sends/Calendar changes, scope-aware setup and verified external write outcomes.
- Deterministic 2–6 seat no-limit Hold'em with hidden-card isolation, legal betting, all-ins, side pots, hand evaluation, persistent tables and bounded local-model strategy decisions.
- Local friction reports, regression candidates, idle persistent scheduling and consistent backups including nested databases and pending file replacements.
- Custom dark navy/violet interface, separate desktop panel preferences, compact-screen drawers and real setup/error states for absent providers.
- Single-command Windows launcher, developer guidance, provider setup documentation, actual UI screenshot and separate hashed CI dependencies.
- Automated verification: **224 backend tests and 28 frontend tests pass**; Ruff, backend mypy, TypeScript production build and frontend formatting pass. GitHub's Linux CI passed with the universal hashed dependency lock, including the fresh functional fixes (runs 37120189618 and 37120508465).
- Live Windows/API/UI verification: native GPU chat, attached-file answers with sources, memory edit/revision/restart, confirmed file edit/original revision, native conversation summary, 1024-dimensional memory/document embeddings, complete legal AI Poker hand without fallback, harness reports and launcher shutdown/restart.
- Desktop 1536×1024 and compact 390×844 rendered checks; corrected model-control wrapping and overlapping mobile drawers. Optional services display their genuine unconfigured state.
- Final in-app browser page identity, meaningful DOM, overlay, console and interaction checks passed. Independently toggled desktop panels survive reload; compact navigation closes its drawer and the exposed backdrop dismisses context.
- Concept/render inspection compared copy, rail/header layout, typography, navy/violet palette, icon treatment, spacing and composer geometry. Intentional additions are functional history/search/archive controls and the installed alias's Lite label; empty Send is disabled. Default 1280×720 was also checked.
- Successful native summaries persist independently of extraction. Automatic memories require an actual user-message source from their processed batch; unsupported candidates are skipped. Manual memories retain optional provenance.
- Fresh real UI/API checks covered CSV task creation and browser download, all six document formats with download/reopen/parse verification, and complete three-player Poker games. The official model completed a legal all-in showdown without fallback; chip totals stayed at 3,000.
- Corrected Poker raise bounds after opponent raises and removed next-hand advancement from exhausted tables. Live UI checks verified the raise value follows the new legal minimum and both human-win and human-bust tables show their completed state.
- Chat validates tool protocol throughout the streamed response and resets rejected partial text before one bounded repair. Provider filtering discards separate and inline reasoning channels, including split and unterminated tags, while preserving public streaming and literal code/JSON examples.
- Generated artifacts retain their requested names when sharing identical bytes. Real UI task/download/reopen checks verified `functionality-final.csv`, its exact two rows, the unchanged earlier filename and distinct records sharing one immutable blob. Per-record parse metadata and tests cover deletion/edit/revision isolation.
- The final official-model conversation passed CSV creation followed by a three-step planning request, with every step below 15 words and no persisted reasoning or stale file claim. Ollama reported 100% GPU inference with an 8K context. A clean browser session had no console warnings/errors or horizontal overflow at 1536×1024; Poker state survived the app restart.

## Currently working

- None for the requested functional verification. Tested fixes are committed and pushed; optional provider setup remains below.

## Blocked on account or system setup

- **Core application:** no installation blocker remains on this machine.
- **Web research:** SearXNG is not installed/running. The documented Windows container setup requires Docker Desktop/WSL 2 and administrator/system setup.
- **Image generation:** ComfyUI and image checkpoints are not installed/running. The adapter and workflow configuration are implemented, but no real generated image is claimed.
- **Google:** Cloud project/OAuth credentials, personal login and consent require the owner. Live Gmail/Calendar account operations remain unverified; provider and approval behavior are covered with isolated tests.
- A vision model is optional and currently unassigned. Scanned-document/OCR fallback may need first-use parsing model downloads.

## Later

- Native Tauri packaging.
- Optional sandboxed code execution and maintenance patch proposals.
- Additional game plugins and MCP tools.
- In-app backup restore/path relocation and native packaged distributions; current documented restore uses the same configured data directory.

## Architectural decisions

- Bind backend to 127.0.0.1; use relative `/api` URLs through the Vite proxy.
- SQLite WAL, foreign keys and migrations; runtime data lives outside versioned source.
- Providers own integrations; application owns routing, context budgets and permissions.
- Ollama is the only inference backend. No commercial LLM fallback or telemetry.
- Start with Lite defaults, one loaded model and bounded context on this machine.
- Unavailable optional services display real setup states, never fake successful output.
- Root agent owns commits; parallel workers own separate source modules.
- Lite prefers the installed official LiquidAI registry model with its native LFM renderer/parser. A multi-turn planning follow-up passed with that model after exposing stale artifact claims with the old handwritten HF alias template. The optional same-weight alias now also uses the native renderer/parser; a real schema-constrained CSV inference passed on Ollama 0.32.14.
- External integrations stay optional and explicitly require their own service/account setup. Tests never substitute fake data into normal application behavior.
- Maintenance output remains reviewable recommendations; the harness does not automatically modify or merge source.
