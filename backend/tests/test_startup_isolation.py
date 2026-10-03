import os
from pathlib import Path


def test_collection_global_app_and_image_recovery_use_the_owned_test_directory():
    from pixel_station.app import app

    owned = Path(os.environ["PIXEL_STATION_DATA"]).resolve()
    assert owned.name.startswith("pixel-station-tests-")
    assert app.state.data_dir == owned
    assert app.state.integration_services.images.root == owned / "images"
    assert Path(app.state.database.engine.url.database).parent == owned
    assert owned != Path(__file__).resolve().parents[2] / "data"
