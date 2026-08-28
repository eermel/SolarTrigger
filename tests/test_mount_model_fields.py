from __future__ import annotations

from copy import deepcopy

import pytest

from backend.mount_worker_runtime import MountWorkerRuntime
from backend.rig_config import load, save, validate


def _config_with_mount(mount, *, rig_id=1):
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
        "sequence": {"common": {}},
        "rigs": [
            {
                "rig_id": rig_id,
                "name": f"RIG {rig_id}",
                "enabled": False,
                "devices": {"camera": None, "mount": mount, "focuser": None},
                "optics": {},
                "photo": {},
            }
        ],
    }


@pytest.mark.parametrize(
    "mount",
    [
        None,
        {"control": "none"},
        {
            "backend": "indi",
            "serial": "X",
            "control": "indi",
            "geometry": "equatorial",
        },
        {"control": "external", "geometry": "altaz", "tracking": "solar"},
    ],
    ids=["tripod", "explicit_none", "indi_equatorial", "external_altaz_solar"],
)
def test_mount_fields_validate_and_round_trip(tmp_path, mount):
    config = _config_with_mount(deepcopy(mount))
    path = tmp_path / "rig.json"

    validate(config)
    save(path, config)
    loaded = load(path)

    assert loaded["rigs"][0]["devices"]["mount"] == mount


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("control", "manual"),
        ("geometry", "fork"),
        ("tracking", "lunar"),
    ],
)
def test_invalid_mount_field_value_raises(field, value):
    config = _config_with_mount({field: value})

    with pytest.raises(ValueError, match=rf"devices\.mount\.{field} must be one of"):
        validate(config)


def test_desired_bindings_include_only_indi_mount_with_identity():
    config = _config_with_mount(
        {
            "backend": "indi",
            "serial": "X",
            "control": "indi",
            "geometry": "equatorial",
        }
    )
    config["rigs"][0]["enabled"] = True
    config["rigs"][0]["devices"]["camera"] = {}
    external_rig = deepcopy(config["rigs"][0])
    external_rig.update({"rig_id": 2, "name": "RIG 2"})
    external_rig["devices"]["mount"] = {
        "control": "external",
        "geometry": "altaz",
        "tracking": "solar",
    }
    config["rigs"].append(external_rig)

    validate(config)
    desired = MountWorkerRuntime._desired_bindings(config)

    assert list(desired) == [("indi", ("serial", "X"))]
    assert next(iter(desired.values())).rig_id == 1
