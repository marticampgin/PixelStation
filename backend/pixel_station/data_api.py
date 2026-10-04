import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .database import now

router = APIRouter(prefix="/api/data", tags=["data"])


def make_backup(app) -> Path:
    data_dir = app.state.data_dir
    target_dir = data_dir / "backups"
    target_dir.mkdir(exist_ok=True)
    destination = target_dir / ("pixel-station-" + now().replace(":", "-") + ".zip")
    selected = list(data_dir.glob("*.db")) + list(data_dir.glob("*.sqlite3"))
    # Reviewed Gmail attachments are immutable application data. Include just
    # their snapshot directory; connector credentials remain outside backups.
    for folder in (
        "files", "file_edits", "images", "generated", "workflows",
        "connectors/google/attachments",
    ):
        root = data_dir / folder
        if root.is_dir() and not root.is_symlink():
            selected.extend(root.rglob("*"))
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in selected:
            if (
                not path.is_file()
                or path.is_symlink()
                or not path.resolve().is_relative_to(data_dir)
            ):
                continue
            if path.name.endswith(("-wal", "-shm", "-journal")):
                continue
            relative = str(path.relative_to(data_dir))
            if path.suffix in {".db", ".sqlite3"}:
                snapshot = target_dir / f"snapshot-{uuid4().hex}.db"
                try:
                    with (
                        closing(sqlite3.connect(path)) as source,
                        closing(sqlite3.connect(snapshot)) as target,
                    ):
                        source.backup(target)
                    archive.write(snapshot, relative)
                finally:
                    snapshot.unlink(missing_ok=True)
            else:
                archive.write(path, relative)
    return destination


@router.get("")
def inspect_data(request: Request):
    return {
        "path": str(request.app.state.data_dir),
        "database": str(request.app.state.database.path),
        "backup_directory": str(request.app.state.data_dir / "backups"),
    }


@router.post("/backup")
def backup(request: Request):
    destination = make_backup(request.app)
    return {
        "filename": destination.name,
        "path": str(destination),
        "size": destination.stat().st_size,
    }


@router.get("/export")
def export(request: Request):
    destination = make_backup(request.app)
    return FileResponse(destination, media_type="application/zip", filename=destination.name)


class ClearInput(BaseModel):
    confirmed: bool = False


@router.post("/clear-cache")
def clear_cache(request: Request, payload: ClearInput):
    if not payload.confirmed:
        raise HTTPException(409, "Confirm cache removal")
    root = (request.app.state.data_dir / "cache").resolve()
    if not root.is_relative_to(request.app.state.data_dir):
        raise HTTPException(400, "Invalid cache path")
    removed = 0
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                path.unlink()
                removed += 1
    request.app.state.llm._cache.clear()
    return {"removed": removed}
