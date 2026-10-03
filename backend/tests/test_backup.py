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
