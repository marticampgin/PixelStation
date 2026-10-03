"""Redirect import-time application setup before pytest collects test modules.

Some modules import the global FastAPI app. Its settings/recovery initialization
must never open a running user's data directory, including during collection.
"""

import gc
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

_original_data_directory = os.environ.get("PIXEL_STATION_DATA")
_session_directory = TemporaryDirectory(prefix="pixel-station-tests-")
_owned_session_path = Path(_session_directory.name).resolve()
os.environ["PIXEL_STATION_DATA"] = str(_owned_session_path)


def pytest_sessionfinish(session, exitstatus):
    # Close import-time SQLite handles before removing our own directory on Windows.
    module = sys.modules.get("pixel_station.app")
    global_app = getattr(module, "app", None)
    if global_app is not None and global_app.state.data_dir == _owned_session_path:
        global_app.state.database.engine.dispose()
    if _original_data_directory is None:
        os.environ.pop("PIXEL_STATION_DATA", None)
    else:
        os.environ["PIXEL_STATION_DATA"] = _original_data_directory
    gc.collect()
    # Retain the exact directory we created; do not derive a cleanup target from env.
    assert Path(_session_directory.name).resolve() == _owned_session_path
    _session_directory.cleanup()
