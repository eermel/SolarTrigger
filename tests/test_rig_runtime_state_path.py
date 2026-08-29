from backend.rig_runtime import _resolve_state_file


def test_state_file_source_layout(tmp_path):
    trigger_dir = tmp_path / "project"
    flask_app = trigger_dir / "flask_app"
    flask_app.mkdir(parents=True)
    (flask_app / "app.py").touch()

    assert _resolve_state_file(trigger_dir) == (
        trigger_dir / "flask_app" / "state.json"
    )


def test_state_file_production_layout(tmp_path):
    trigger_dir = tmp_path / "solar-eclipse-trigger-prod"
    trigger_dir.mkdir()
    (trigger_dir / "app.py").touch()

    assert _resolve_state_file(trigger_dir) == (
        trigger_dir / "state.json"
    )


def test_state_file_defaults_to_source_layout_when_layout_is_not_installed(tmp_path):
    trigger_dir = tmp_path / "project"
    trigger_dir.mkdir()

    assert _resolve_state_file(trigger_dir) == (
        trigger_dir / "flask_app" / "state.json"
    )
