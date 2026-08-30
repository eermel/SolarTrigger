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


def test_preview_applies_same_fixed_trailing_ceiling_as_runtime(
    preview_api,
    monkeypatch,
):
    client = preview_api(atmos_enabled=False)

    config = flask_module.load_rig_configuration()
    config["rigs"][0]["devices"]["camera"] = {
        "manufacturer": "Test Cameras",
        "model": "Known Model",
    }
    config["rigs"][0]["optics"] = {
        "focal_length_mm": 1000.0,
    }
    config["rigs"][0]["photo"].update({
        "anti_trailing_enabled": True,
        "motion_tolerance_px": 1.0,
        "iso_compensation_enabled": False,
        "iso_max": 6400,
    })

    monkeypatch.setattr(
        flask_module,
        "load_rig_configuration",
        lambda: config,
    )
    monkeypatch.setattr(
        flask_module,
        "compute_motion_exposure_ceiling",
        lambda *_args, **_kwargs: 0.25,
    )

    response = client.post(
        "/api/rigs/preview",
        json={
            "intents": [
                _regular_intent(
                    shutter_min="1",
                    shutter_max="1/1000",
                    iso_target=200,
                )
            ]
        },
    )

    assert response.status_code == 200
    item = response.get_json()["rigs"][0]["items"][0]

    assert item["error"] is None
    assert item["motion_policy"] == "fixed_trailing"
    assert max(item["exposures_s"]) == pytest.approx(0.25)
    assert item["iso_applied"] == "200"
    assert item["corrections"] == ["shutter_limited"]


def test_preview_returns_visible_differential_lines(
    preview_api,
    monkeypatch,
):
    client = preview_api(atmos_enabled=False)

    config = flask_module.load_rig_configuration()
    config["rigs"][0]["devices"]["camera"] = {
        "backend": "simulated",
        "manufacturer": "Test Cameras",
        "model": "Known Model",
    }
    config["rigs"][0]["optics"] = {
        "focal_length_mm": 1000.0,
    }
    config["rigs"][0]["photo"].update({
        "anti_trailing_enabled": True,
        "motion_tolerance_px": 1.0,
        "iso_compensation_enabled": False,
        "iso_max": 6400,
    })

    monkeypatch.setattr(
        flask_module,
        "load_rig_configuration",
        lambda: config,
    )
    monkeypatch.setattr(
        flask_module,
        "compute_motion_exposure_ceiling",
        lambda *_args, **_kwargs: 0.25,
    )

    response = client.post(
        "/api/rigs/preview",
        json={
            "intents": [
                _regular_intent(
                    shutter_min="1",
                    shutter_max="1/1000",
                    iso_target=200,
                )
            ]
        },
    )

    assert response.status_code == 200

    item = response.get_json()["rigs"][0]["items"][0]

    assert item["error"] is None
    assert isinstance(item["diff_lines"], list)
    assert item["diff_lines"]
    assert all(
        "motion_policy" not in line
        and "corrections" not in line
        and "warnings" not in line
        for line in item["diff_lines"]
    )


def test_preview_rig_override_is_ephemeral(preview_api):
    client = preview_api(atmos_enabled=False)

    before = flask_module.load_rig_configuration()
    assert before["rigs"][0]["optics"] == {
        "focal_length_mm": None,
    }

    response = client.post(
        "/api/rigs/preview",
        json={
            "intents": [_regular_intent()],
            "rig_id": 1,
            "rig_override": {
                "optics": {
                    "focal_length_mm": 430.0,
                },
                "photo": {
                    "anti_trailing_enabled": False,
                    "motion_tolerance_px": 0.5,
                    "iso_compensation_enabled": False,
                    "iso_max": 3200,
                    "atmos_enabled": False,
                },
            },
        },
    )

    assert response.status_code == 200
    payload = response.get_json()

    assert [rig["rig_id"] for rig in payload["rigs"]] == [1]

    after = flask_module.load_rig_configuration()

    # Preview must never modify the persisted RIG configuration.
    assert after == before
    assert after["rigs"][0]["optics"] == {
        "focal_length_mm": None,
    }
