# Pixel Station implementation status

## Completed

- Inspected Windows environment and empty GitHub repository.
- Verified NVIDIA driver 617.14, RTX 3060 Laptop GPU (6 GB), 8 GB RAM.
- Established local-first architecture and runtime-data exclusions.

## Currently working

- FastAPI / SQLAlchemy / Alembic backend and persistent streaming chat.
- React / TypeScript / Vite visual shell.
- Provider boundaries, permissions, files, memory and integration setup states.
- Single-command local launcher, automated verification and documentation.

## Blocked on account or system setup

- Google login and consent require the owner's participation.
- Docker / WSL installation requires Windows administrator approval if using the documented SearXNG container setup.

## Later

- Native Tauri packaging.
- Optional sandboxed code execution and maintenance patch proposals.
- Additional game plugins and MCP tools.

## Architectural decisions

- Bind backend to 127.0.0.1; use relative `/api` URLs through the Vite proxy.
- SQLite WAL, foreign keys and migrations; runtime data lives outside versioned source.
- Providers own integrations; application owns routing, context budgets and permissions.
- Ollama is the only inference backend. No commercial LLM fallback or telemetry.
- Start with Lite defaults, one loaded model and bounded context on this machine.
- Unavailable optional services display real setup states, never fake successful output.
- Root agent owns commits; parallel workers own separate source modules.
