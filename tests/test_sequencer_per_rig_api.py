import flask_app.app as app_module
from backend.state_store import StateStore


def _client(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        app_module,
        "CONFIGS_DIR",
        tmp_path / "configs",
    )

    monkeypatch.setattr(
        app_module,
        "_state_store",
        StateStore(
            tmp_path / "state.json"
        ),
    )

    return app_module.app.test_client()


def _plan(rig_id):
    return {
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
        "sequence_margin_min": 60,
        "initial_state_required": {
            str(rig_id): {}
        },
        "commands": [
            {
                "time_utc":
                    "2027-08-02T10:00:00.000Z",
                "rig_id": rig_id,
                "action": "PHOTO",
                "params": {
                    "shutter": "1/500"
                },
            }
        ],
        "command_phases":
            ["partial"],
        "camera_timing_files": {
            str(rig_id):
                "sony.json"
        },
    }


def _context(rig_id):
    return {
        "circumstances": {
            "_date": "2027-08-02",
            "C1": "10:00:00.000",
            "C2": "10:02:00.000",
            "TMAX": "10:02:30.000",
            "C3": "10:03:00.000",
            "C4": "10:05:00.000",
            "_circumstances_location":
                {
                    "latitude": 1.0,
                    "longitude": 2.0,
                    "altitude_m": 3,
                },
        },
        "photo_setup": {
            "config_type":
                "photo_setup",
        },
        "exposure_opt": {
            "config_type":
                "exposure_optimization",
        },
        "rig": {
            "rig_id": rig_id,
            "name": f"RIG {rig_id}",
            "enabled": True,
            "devices": {
                "camera": {
                    "backend": "sony",
                    "manufacturer":
                        "Sony Corporation",
                    "model": "ILCE-7M5",
                },
            },
            "optics": {
                "focal_length_mm": 430
            },
            "photo": {},
        },
        "effective_rig": {
            "rig_id": rig_id,
            "photo": {},
        },
    }


def test_per_rig_compile_writes_plan(
    tmp_path,
    monkeypatch,
):
    client = _client(
        monkeypatch,
        tmp_path,
    )

    monkeypatch.setattr(
        app_module,
        "compile_rig_execution_plan_from_files",
        lambda **kwargs: (
            _plan(kwargs["rig_id"]),
            ["line"],
            _context(
                kwargs["rig_id"]
            ),
        ),
    )

    response = client.post(
        "/api/sequencer/compile",
        json={
            "rig_id": 2,
            "plan_name": "Egypt final",
            "circumstances_file":
                "test.json",
            "photo_setup_file":
                "photo.json",
            "exposure_opt_file":
                "expo.json",
            "sequence_margin_min": 60,
        },
    )

    assert response.status_code == 200

    data = response.get_json()

    assert data["filename"] == (
        "exec_plan_20270802_"
        "RIG2_SONY_Egypt_final.plan"
    )

    saved = (
        tmp_path
        / "configs"
        / "execution_plan"
        / data["filename"]
    )

    assert saved.is_file()

    assert (
        "SolarTrigger Execution Plan"
        in saved.read_text(
            encoding="utf-8"
        )
    )

    restored = StateStore(
        tmp_path / "state.json"
    )

    assert (
        restored.get(
            "execution_plan_file_rig_2"
        )
        == data["filename"]
    )


def test_clean_one_rig_only(
    tmp_path,
    monkeypatch,
):
    client = _client(
        monkeypatch,
        tmp_path,
    )

    base = (
        tmp_path
        / "configs"
        / "execution_plan"
    )

    base.mkdir(parents=True)

    rig1 = (
        base
        / "exec_plan_20270802_RIG1_SONY_a.plan"
    )
    rig2a = (
        base
        / "exec_plan_20270802_RIG2_SONY_a.plan"
    )
    rig2b = (
        base
        / "exec_plan_20270802_RIG2_NIKON_b.plan"
    )
    unrelated = (
        base / "manual.plan"
    )

    for path in (
        rig1,
        rig2a,
        rig2b,
        unrelated,
    ):
        path.write_text(
            "x",
            encoding="utf-8",
        )

    app_module._state_store.set(
        "execution_plan_file_rig_2",
        rig2a.name,
        persist=True,
    )

    response = client.post(
        "/api/configs/execution_plan/clean",
        json={"rig_id": 2},
    )

    assert response.status_code == 200
    assert (
        response.get_json()["deleted"]
        == 2
    )

    assert rig1.exists()
    assert unrelated.exists()
    assert not rig2a.exists()
    assert not rig2b.exists()


def test_plan_list_supports_plan_and_json(
    tmp_path,
    monkeypatch,
):
    client = _client(
        monkeypatch,
        tmp_path,
    )

    base = (
        tmp_path
        / "configs"
        / "execution_plan"
    )

    base.mkdir(parents=True)

    (
        base / "new.plan"
    ).write_text("x")

    (
        base / "legacy.json"
    ).write_text("{}")

    (
        base / "ignore.txt"
    ).write_text("x")

    response = client.get(
        "/api/configs/execution_plan/list"
    )

    assert response.status_code == 200

    assert (
        response.get_json()["files"]
        == [
            "legacy.json",
            "new.plan",
        ]
    )
