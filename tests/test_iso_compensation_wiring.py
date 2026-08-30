import re
from pathlib import Path

from backend.plan_cache import rig_plan_version


ROOT = Path(__file__).resolve().parents[1]


def _source(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_iso_compensation_changes_plan_version():
    base = {
        "photo": {
            "anti_trailing_enabled": True,
            "motion_tolerance_px": 1.0,
            "iso_max": 6400,
            "iso_compensation_enabled": True,
        }
    }
    changed = {
        "photo": {
            **base["photo"],
            "iso_compensation_enabled": False,
        }
    }

    assert rig_plan_version(base) != rig_plan_version(changed)


def test_camera_runtime_snapshot_contains_iso_compensation():
    source = _source("backend/camera_worker_runtime.py")
    assert '"iso_compensation_enabled"' in source


def test_camera_ipc_propagates_iso_compensation_to_materializer():
    source = _source("backend/camera_ipc_server.py")

    assert source.count(
        "iso_compensation_enabled=iso_compensation_enabled"
    ) >= 3

    assert "materialize_exposure_plan(" in source

    assert re.search(
        r'photo\.get\(\s*'
        r'"iso_compensation_enabled"\s*,\s*'
        r'True\s*,?\s*\)',
        source,
    )


def test_api_validates_iso_compensation_as_boolean():
    source = _source("flask_app/app.py")
    assert '"iso_compensation_enabled",' in source
    assert "motion_tolerance_px" in source
