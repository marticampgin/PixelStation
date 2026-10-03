import sqlite3
import zipfile
from types import SimpleNamespace

from pixel_station.data_api import make_backup


def test_nested_database_backup_includes_uncheckpointed_wal(tmp_path):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    source = sqlite3.connect(image_dir / "library.sqlite3")
    source.execute("PRAGMA journal_mode=WAL")
    source.execute("CREATE TABLE images(id TEXT)")
    source.execute("INSERT INTO images VALUES('saved-image')")
    source.commit()
    (image_dir / "result.png").write_bytes(b"local-image")
    archive_path = make_backup(SimpleNamespace(state=SimpleNamespace(data_dir=tmp_path)))
    restored = tmp_path / "restore"
    with zipfile.ZipFile(archive_path) as archive:
        assert not any(name.endswith(("-wal", "-shm")) for name in archive.namelist())
        archive.extractall(restored)
    with sqlite3.connect(restored / "images" / "library.sqlite3") as target:
        assert target.execute("SELECT id FROM images").fetchone()[0] == "saved-image"
    source.close()


def test_backup_preserves_pending_validated_file_edit(tmp_path):
    from fastapi.testclient import TestClient

    from pixel_station.app import create_app

    app = create_app(tmp_path, discover=False)
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/files/upload", files={"file": ("notes.txt", b"Original revision", "text/plain")}
        ).json()
        proposal_response = client.post(
            f"/api/files/{uploaded['id']}/edit-proposals",
            json={"content": "Reviewed replacement", "plan": "Update verification notes"},
        )
        assert proposal_response.status_code == 200
        proposal = proposal_response.json()
        replacement = tmp_path / "file_edits" / "proposals" / proposal["id"] / "replacement.txt"
        archive_path = make_backup(app)
        replacement.unlink()
        with zipfile.ZipFile(archive_path) as archive:
            replacement.write_bytes(archive.read(replacement.relative_to(tmp_path).as_posix()))
        confirmed = client.post(
            f"/api/files/edit-proposals/{proposal['id']}/confirm", json={"confirmed": True}
        )
        assert confirmed.status_code == 200
        assert client.get(f"/api/files/{uploaded['id']}/content").content == b"Reviewed replacement"
        revisions = client.get(f"/api/files/{uploaded['id']}/revisions").json()
        assert len(revisions) == 1
        assert client.get(
            f"/api/files/{uploaded['id']}/revisions/{revisions[0]['id']}/content"
        ).content == b"Original revision"
