import json

import backend.sequencer_plan_service as service


def _write(path, payload):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_compile_rig_wrapper_isolates_one_rig(
    tmp_path,
    monkeypatch,
):
    configs = tmp_path / "configs"

    circumstances = {
        "_date": "2027-08-02",
        "C1": "10:00:00.000",
        "C2": "10:02:00.000",
        "TMAX": "10:02:30.000",
        "C3": "10:03:00.000",
        "C4": "10:05:00.000",
    }

    photo = {
        "schema_version": 2,
        "config_type": "photo_setup",
        "phases": {},
    }

    exposure = {
        "schema_version": 1,
        "config_type":
            "exposure_optimization",
        "atmospheric_attenuation_enabled":
            True,
        "rigs": [
            {
                "rig_id": 2,
                "photo": {
                    "iso_compensation_enabled":
                        False,
                },
            }
        ],
    }

    _write(
        configs
        / "circumstances"
        / "test.json",
        circumstances,
    )
    _write(
        configs
        / "photo_cfg"
        / "photo.json",
        photo,
    )
    _write(
        configs
        / "exposure_opt"
        / "expo.json",
        exposure,
    )

    rig1 = {
        "rig_id": 1,
        "name": "RIG 1",
        "enabled": True,
        "devices": {
            "camera": {
                "backend": "sony",
                "manufacturer": "Sony",
                "model": "A",
            },
        },
        "optics": {
            "focal_length_mm": 100
        },
        "photo": {},
    }

    rig2 = {
        "rig_id": 2,
        "name": "RIG 2",
        "enabled": True,
        "devices": {
            "camera": {
                "backend": "sony",
                "manufacturer": "Sony",
                "model": "B",
            },
        },
        "optics": {
            "focal_length_mm": 200
        },
        "photo": {
            "iso_compensation_enabled":
                True,
        },
    }

    received = {}

    def fake_compile(**kwargs):
        received.update(kwargs)

        return (
            {
                "schema_version": 2,
                "config_type":
                    "execution_plan",
                "sources": {
                    "circumstances_file":
                        "test.json",
                    "photo_setup_file":
                        "photo.json",
                    "exposure_opt_file":
                        "expo.json",
                },
                "sequence_start_utc":
                    "2027-08-02T09:00:00.000Z",
                "sequence_end_utc":
                    "2027-08-02T11:00:00.000Z",
                "initial_state_required":
                    {"2": {}},
                "commands": [
                    {
                        "time_utc":
                            "2027-08-02T10:00:00.000Z",
                        "rig_id": 2,
                        "action": "PHOTO",
                        "params": {},
                    }
                ],
                "command_phases":
                    ["partial"],
                "camera_timing_files":
                    {"2": "sony_b.json"},
            },
            ["plan line"],
        )

    monkeypatch.setattr(
        service,
        "compile_execution_plan_from_files",
        fake_compile,
    )

    (
        plan,
        _lines,
        context,
    ) = (
        service
        .compile_rig_execution_plan_from_files(
            configs_dir=configs,
            rig_id=2,
            circumstances_file=
                "test.json",
            photo_setup_file=
                "photo.json",
            exposure_opt_file=
                "expo.json",
            sequence_margin_min=60,
            rig_config={
                "rigs": [rig1, rig2],
                "sequence":
                    {"common": {}},
                "eclipse": None,
            },
        )
    )

    assert [
        item["rig_id"]
        for item in received[
            "rig_config"
        ]["rigs"]
    ] == [2]

    assert (
        plan["commands"][0][
            "rig_id"
        ]
        == 2
    )

    assert (
        context["circumstances"]
        == circumstances
    )

    assert (
        context["rig"]["rig_id"]
        == 2
    )

    assert (
        context[
            "effective_rig"
        ]["photo"][
            "iso_compensation_enabled"
        ]
        is False
    )

    assert (
        context[
            "effective_rig"
        ]["photo"][
            "atmos_enabled"
        ]
        is True
    )
