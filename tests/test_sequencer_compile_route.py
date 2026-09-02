import json

import flask_app.app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(
        app_module,
        "CONFIGS_DIR",
        tmp_path / "configs",
    )

    return app_module.app.test_client()


def test_compile_route_persists_execution_plan(
    tmp_path,
    monkeypatch,
):
    client = _client(
        monkeypatch,
        tmp_path,
    )

    plan = {
        "schema_version": 1,
        "config_type": "execution_plan",
        "events": [],
        "targets": [],
    }

    lines = [
        "10:00:00.000 | RIG1 | TARGET | PARTIAL",
    ]

    received = {}

    def compile_plan(**kwargs):
        received.update(kwargs)
        return plan, lines

    monkeypatch.setattr(
        app_module,
        "compile_execution_plan_from_files",
        compile_plan,
    )

    response = client.post(
        "/api/sequencer/compile",
        json={
            "sequence_file": "sequence_dryrun_short.json",
            "camera_timing_files": {
                "1": "sony_a7v.json",
            },
        },
    )

    assert response.status_code == 200

    data = response.get_json()

    assert data["status"] == "ok"
    assert (
        data["filename"]
        == "execution_plan_sequence_dryrun_short.json"
    )
    assert data["plan"] == plan
    assert data["lines"] == lines

    assert received == {
        "configs_dir": tmp_path / "configs",
        "sequence_file": "sequence_dryrun_short.json",
        "camera_timing_files": {
            1: "sony_a7v.json",
        },
    }

    saved = (
        tmp_path
        / "configs"
        / "execution_plan"
        / "execution_plan_sequence_dryrun_short.json"
    )

    assert saved.exists()

    assert json.loads(
        saved.read_text(encoding="utf-8")
    ) == plan


def test_compile_route_requires_sequence_file(
    tmp_path,
    monkeypatch,
):
    client = _client(
        monkeypatch,
        tmp_path,
    )

    response = client.post(
        "/api/sequencer/compile",
        json={
            "camera_timing_files": {
                "1": "sony.json",
            },
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == (
        "Missing Sequencer input: sequence_file"
    )


def test_compile_route_requires_timing_mapping(
    tmp_path,
    monkeypatch,
):
    client = _client(
        monkeypatch,
        tmp_path,
    )

    response = client.post(
        "/api/sequencer/compile",
        json={
            "sequence_file": "sequence_dryrun_short.json",
        },
    )

    assert response.status_code == 400

    assert (
        response.get_json()["error"]
        == "camera_timing_files must be an object"
    )


def test_compile_route_maps_compile_error_to_400(
    tmp_path,
    monkeypatch,
):
    client = _client(
        monkeypatch,
        tmp_path,
    )

    def fail(**_kwargs):
        raise app_module.SequencerCompileError(
            "missing calibrated camera timing profile for RIG 1"
        )

    monkeypatch.setattr(
        app_module,
        "compile_execution_plan_from_files",
        fail,
    )

    response = client.post(
        "/api/sequencer/compile",
        json={
            "sequence_file": "sequence_dryrun_short.json",
            "camera_timing_files": {
                "1": "sony.json",
            },
        },
    )

    assert response.status_code == 400

    data = response.get_json()

    assert data["code"] == "SEQUENCER_COMPILE_FAILED"


def test_compile_route_rejects_timing_path_traversal(
    tmp_path,
    monkeypatch,
):
    client = _client(
        monkeypatch,
        tmp_path,
    )

    response = client.post(
        "/api/sequencer/compile",
        json={
            "sequence_file": "sequence_dryrun_short.json",
            "camera_timing_files": {
                "1": "../sony.json",
            },
        },
    )

    assert response.status_code == 400


def test_compile_route_rejects_sequence_path_traversal(
    tmp_path,
    monkeypatch,
):
    client = _client(
        monkeypatch,
        tmp_path,
    )

    response = client.post(
        "/api/sequencer/compile",
        json={
            "sequence_file": "../sequence.json",
            "camera_timing_files": {
                "1": "sony.json",
            },
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == (
        "Invalid Sequencer input filename: sequence_file"
    )


def test_camera_timing_list_route(
    tmp_path,
    monkeypatch,
):
    client = _client(monkeypatch, tmp_path)

    timing_dir = (
        tmp_path
        / "configs"
        / "camera_timing"
    )
    timing_dir.mkdir(parents=True)

    (timing_dir / "sony_b.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (timing_dir / "sony_a.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (timing_dir / "ignore.txt").write_text(
        "x",
        encoding="utf-8",
    )

    response = client.get(
        "/api/configs/list_camera_timing"
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "files": [
            "sony_a.json",
            "sony_b.json",
        ],
    }
