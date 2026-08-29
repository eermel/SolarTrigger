import json
import sys
from copy import deepcopy
from types import ModuleType

import pytest

from backend.rig_manager import RigManager


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


def _config():
    return {
        "schema_version": 2,
        "eclipse": {
            "date": "2027-08-02",
            "reference_site": {"lat": 43.6, "lon": 1.44, "alt_m": 150},
            "circumstances": {
                "C1": "08:00:00",
                "C2": "09:00:00",
                "TMAX": "09:01:00",
                "C3": "09:02:00",
                "C4": "10:00:00",
            },
        },
        "sequence": {"common": {}},
        "rigs": [{
            "rig_id": 1,
            "enabled": True,
            "name": "D850 rig",
            "devices": {
                "camera": {
                    "backend": "nikon",
                    "model": "Nikon D850",
                    "serial": "D850-STABLE",
                },
                "mount": None,
                "focuser": None,
            },
            "optics": {},
            "photo": {},
        }],
    }


@pytest.fixture
def inventory_api(tmp_path, monkeypatch):
    config = _config()
    config_path = tmp_path / "configs" / "rig" / "default.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(flask_module, "TRIGGER_DIR", tmp_path)
    monkeypatch.setattr(
        flask_module, "get_rig_manager", lambda: RigManager.from_config(config)
    )
    return flask_module.app.test_client(), config_path


def test_get_reflects_hotplug_presence_without_persisting_it(
    inventory_api, monkeypatch
):
    client, config_path = inventory_api
    before = config_path.read_bytes()
    snapshots = iter([
        {"camera": [], "mount": [], "focuser": []},
        {
            "camera": [{
                "category": "camera",
                "backend": "nikon",
                "model": "Nikon D850",
                "serial": "D850-STABLE",
                "present": True,
                "transport_locator": "usb:003,012",
            }],
            "mount": [],
            "focuser": [],
        },
    ])
    monkeypatch.setattr(
        flask_module, "get_cached_inventory", lambda: deepcopy(next(snapshots))
    )

    absent = client.get("/api/rigs/devices").get_json()
    present = client.get("/api/rigs/devices").get_json()

    assert absent["rigs"][0]["devices"]["camera"]["present"] is False
    assert present["rigs"][0]["devices"]["camera"]["present"] is True
    assert config_path.read_bytes() == before
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "present" not in saved["rigs"][0]["devices"]["camera"]
    assert "transport_locator" not in json.dumps(saved)


def test_refresh_returns_transport_locator_without_saving_it(
    inventory_api, monkeypatch
):
    client, config_path = inventory_api
    before = config_path.read_bytes()
    refreshed = {
        "camera": [{
            "category": "camera",
            "backend": "nikon",
            "model": "Nikon D850",
            "serial": "D850-STABLE",
            "present": True,
            "transport_locator": "usb:004,021",
        }],
        "mount": [],
        "focuser": [],
    }
    monkeypatch.setattr(
        flask_module, "refresh_inventory", lambda: deepcopy(refreshed)
    )

    response = client.post("/api/rigs/devices/refresh")

    assert response.status_code == 200
    assert response.get_json()["camera"][0]["transport_locator"] == "usb:004,021"
    assert config_path.read_bytes() == before
    assert "transport_locator" not in config_path.read_text(encoding="utf-8")


def test_post_rejects_non_pilotable_inventory_device(
    inventory_api, monkeypatch
):
    client, config_path = inventory_api
    before = config_path.read_bytes()

    monkeypatch.setattr(
        flask_module,
        "load_rig_configuration",
        lambda: deepcopy(_config()),
    )

    inventory = {
        "camera": [{
            "category": "camera",
            "backend": "gphoto2",
            "manufacturer": "Sony",
            "model": "Sony Alpha-A6600 (PC Control)",
            "serial": "CFD1E04011A5",
            "bindable": True,
            "pilotable": False,
            "present": True,
            "transport_locator": "usb:001,015",
        }],
        "mount": [],
        "focuser": [],
    }
    monkeypatch.setattr(
        flask_module,
        "get_cached_inventory",
        lambda: deepcopy(inventory),
    )

    response = client.post(
        "/api/rigs/devices",
        json={
            "rigs": [{
                "rig_id": 3,
                "devices": {
                    "camera": {
                        "category": "camera",
                        "backend": "gphoto2",
                        "manufacturer": "Sony",
                        "model": "Sony Alpha-A6600 (PC Control)",
                        "serial": "CFD1E04011A5",
                    }
                },
            }]
        },
    )

    assert response.status_code == 400
    assert "not pilotable" in response.get_json()["error"].lower()
    assert config_path.read_bytes() == before
