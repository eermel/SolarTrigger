import json

import flask_app.app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(
        app_module,
        "CONFIGS_DIR",
        tmp_path / "configs",
    )
    return app_module.app.test_client()


def _payload(**overrides):
    payload = {
        "circumstances_file": "test.json",
        "photo_setup_file": "photo.json",
        "exposure_opt_file": "expo.json",
        "sequence_margin_min": 5,
        "output_filename": "my_execution_plan.json",
    }
    payload.update(overrides)
    return payload


def test_compile_route_persists_execution_plan(
    tmp_path,
    monkeypatch,
):
    client = _client(monkeypatch, tmp_path)

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
        json=_payload(),
    )

    assert response.status_code == 200

    data = response.get_json()

    assert data["status"] == "ok"
    assert data["filename"] == "my_execution_plan.json"
    assert data["plan"] == plan
    assert data["lines"] == lines

    assert received == {
        "configs_dir": tmp_path / "configs",
        "circumstances_file": "test.json",
        "photo_setup_file": "photo.json",
        "exposure_opt_file": "expo.json",
        "sequence_margin_min": 5,
        "sequence_file": None,
    }

    saved = (
        tmp_path
        / "configs"
        / "execution_plan"
        / "my_execution_plan.json"
    )

    assert saved.exists()

    assert json.loads(
        saved.read_text(encoding="utf-8")
    ) == plan


def test_compile_route_sequence_file_is_optional(
    tmp_path,
    monkeypatch,
):
    client = _client(monkeypatch, tmp_path)

    monkeypatch.setattr(
        app_module,
        "compile_execution_plan_from_files",
        lambda **_kwargs: (
            {
                "schema_version": 1,
                "config_type": "execution_plan",
                "events": [],
                "targets": [],
            },
            [],
        ),
    )

    response = client.post(
        "/api/sequencer/compile",
        json=_payload(),
    )

    assert response.status_code == 200


def test_compile_route_adds_json_extension(
    tmp_path,
    monkeypatch,
):
    client = _client(monkeypatch, tmp_path)

    monkeypatch.setattr(
        app_module,
        "compile_execution_plan_from_files",
        lambda **_kwargs: (
            {
                "schema_version": 1,
                "config_type": "execution_plan",
                "events": [],
                "targets": [],
            },
            [],
        ),
    )

    response = client.post(
        "/api/sequencer/compile",
        json=_payload(
            output_filename="user_selected_plan",
        ),
    )

    assert response.status_code == 200
    assert (
        response.get_json()["filename"]
        == "user_selected_plan.json"
    )

    assert (
        tmp_path
        / "configs"
        / "execution_plan"
        / "user_selected_plan.json"
    ).exists()


def test_compile_route_requires_current_inputs(
    tmp_path,
    monkeypatch,
):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/sequencer/compile",
        json=_payload(
            circumstances_file="",
        ),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == (
        "Missing Sequencer input: circumstances_file"
    )


def test_compile_route_requires_output_filename(
    tmp_path,
    monkeypatch,
):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/sequencer/compile",
        json=_payload(
            output_filename="",
        ),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == (
        "Missing Sequencer input: output_filename"
    )



def test_compile_route_maps_compile_error_to_400(
    tmp_path,
    monkeypatch,
):
    client = _client(monkeypatch, tmp_path)

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
        json=_payload(),
    )

    assert response.status_code == 400

    data = response.get_json()

    assert data["code"] == "SEQUENCER_COMPILE_FAILED"



def test_compile_route_rejects_input_path_traversal(
    tmp_path,
    monkeypatch,
):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/sequencer/compile",
        json=_payload(
            circumstances_file="../test.json",
        ),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == (
        "Invalid Sequencer input filename: circumstances_file"
    )


def test_compile_route_rejects_output_path_traversal(
    tmp_path,
    monkeypatch,
):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/sequencer/compile",
        json=_payload(
            output_filename="../plan.json",
        ),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == (
        "Invalid execution plan filename"
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
