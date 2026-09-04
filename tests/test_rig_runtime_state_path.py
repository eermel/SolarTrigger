from backend.rig_runtime import _resolve_state_file


def test_state_file_source_layout(tmp_path):
    trigger_dir = tmp_path / "project"

    assert _resolve_state_file(trigger_dir) == (
        trigger_dir / "var" / "state" / "state.json"
    )


def test_state_file_production_layout(tmp_path):
    trigger_dir = tmp_path / "solar-eclipse-trigger-prod"

    assert _resolve_state_file(trigger_dir) == (
        trigger_dir / "var" / "state" / "state.json"
    )


def test_state_file_does_not_depend_on_application_layout(tmp_path):
    trigger_dir = tmp_path / "project"

    (trigger_dir / "flask_app").mkdir(parents=True)
    (trigger_dir / "flask_app" / "app.py").touch()
    (trigger_dir / "app.py").touch()

    assert _resolve_state_file(trigger_dir) == (
        trigger_dir / "var" / "state" / "state.json"
    )
