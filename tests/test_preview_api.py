import builtins
import json
import sys
from types import ModuleType

import pytest


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import backend.rig_runtime as rig_runtime
import flask_app.app as flask_module
import plugins.camera as camera_plugins
import plugins.focuser as focuser_plugins
import plugins.mount as mount_plugins


def _configuration(atmos_enabled):
    return {
        "schema_version": 2,
        "eclipse": {
            "date": "2026-08-12",
            "reference_site": {"lat": 44.0, "lon": 2.0, "alt_m": 120.0},
            "circumstances": {
                "C1": "16:00:00",
                "C2": "17:00:00",
                "TMAX": "17:01:00",
                "C3": "17:02:00",
                "C4": "18:00:00",
            },
        },
        "sequence": {"common": {"phases": {"partial": {"iso": 200}}}},
        "rigs": [
            {
                "rig_id": 1,
                "name": "Enabled preview rig",
                "enabled": True,
                "devices": {
                    "camera": {"backend": "simulated", "serial": "PREVIEW-1"},
                    "mount": None,
                    "focuser": None,
                },
                "optics": {},
                "photo": {"atmos_enabled": atmos_enabled},
            },
            {
                "rig_id": 2,
                "name": "Disabled preview rig",
                "enabled": False,
                "devices": {"camera": None, "mount": None, "focuser": None},
                "optics": {},
                "photo": {"atmos_enabled": True},
            },
        ],
    }


def _regular_intent(**updates):
    intent = {
        "phase": "partial",
        "target_time": "2026-08-12T17:30:00Z",
        "deadline": "2026-08-12T17:29:59Z",
        "shutter_min": "1/125",
        "shutter_max": "1/1000",
        "iso_target": 200,
        "request_id": "regular",
    }
    intent.update(updates)
    return intent


@pytest.fixture
def preview_api(tmp_path, monkeypatch):
    def make_client(*, atmos_enabled, include_atmosphere=True):
        config_path = tmp_path / "configs" / "rig" / "default.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(_configuration(atmos_enabled)), encoding="utf-8"
        )

        circumstances = {
            "_date": "2026-08-12",
            "C1": "16:00:00",
            "C2": "17:00:00",
            "TMAX": "17:01:00",
            "C3": "17:02:00",
            "C4": "18:00:00",
        }
        if include_atmosphere:
            circumstances.update({
                "C1_alt_deg": 25.0,
                "C2_alt_deg": 30.0,
                "TMAX_alt_deg": 31.0,
                "C3_alt_deg": 30.0,
                "C4_alt_deg": 25.0,
                "_circumstances_location": {"altitude_m": 120.0},
            })
        eclipse_path = tmp_path / "todayeclipse.json"
        eclipse_path.write_text(json.dumps(circumstances), encoding="utf-8")

        monkeypatch.setattr(rig_runtime, "TRIGGER_DIR", tmp_path)
        monkeypatch.setattr(flask_module, "JSON_FILE", eclipse_path)
        return flask_module.app.test_client()

    return make_client


@pytest.fixture(autouse=True)
def forbid_plugin_and_worker_access(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("preview endpoint must not access plugins or workers")

    for name in (
        "get_camera_worker_runtime",
        "get_focuser_worker_runtime",
        "get_mount_worker_runtime",
    ):
        monkeypatch.setattr(flask_module, name, forbidden)
    monkeypatch.setattr(camera_plugins, "load_plugin", forbidden)
    monkeypatch.setattr(focuser_plugins, "load_focuser", forbidden)
    monkeypatch.setattr(mount_plugins, "load_mount", forbidden)

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("plugins.") or "worker" in name:
            pytest.fail(f"preview endpoint imported runtime module {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


def test_preview_is_deterministic_and_includes_trigger_disabled_rigs(preview_api):
    client = preview_api(atmos_enabled=False)
    payload = {"intents": [_regular_intent()]}

    first = client.post("/api/rigs/preview", json=payload)
    second = client.post("/api/rigs/preview", json=payload)

    assert first.status_code == second.status_code == 200
    assert first.get_json() == second.get_json()
    assert [rig["rig_id"] for rig in first.get_json()["rigs"]] == [1, 2]


def test_preview_without_atmos_keeps_phase_origin_and_has_no_atmos_correction(
    preview_api,
):
    client = preview_api(atmos_enabled=False)

    response = client.post(
        "/api/rigs/preview", json={"intents": [_regular_intent()]}
    )

    assert response.status_code == 200
    item = response.get_json()["rigs"][0]["items"][0]
    assert item["error"] is None
    assert item["origin"] == item["phase"] == "partial"
    assert item["atmos_applied"] is False
    assert "atmos" not in item["corrections"]


def test_missing_atmos_config_is_an_item_error_without_aborting_other_items(
    preview_api,
):
    client = preview_api(atmos_enabled=True, include_atmosphere=False)
    irregular = _regular_intent(
        shutter_min=None,
        shutter_max=None,
        speeds=["1/1000", "1/500", "1/60"],
        request_id="irregular",
    )

    response = client.post(
        "/api/rigs/preview", json={"intents": [_regular_intent(), irregular]}
    )

    assert response.status_code == 200
    assert [rig["rig_id"] for rig in response.get_json()["rigs"]] == [1, 2]
    failed, successful = response.get_json()["rigs"][0]["items"]
    assert failed["request_id"] == "regular"
    assert failed["error"]["code"] == "CONFIG_INVALID"
    assert successful["request_id"] == "irregular"
    assert successful["error"] is None
    assert successful["atmos_applied"] is False
