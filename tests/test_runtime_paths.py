from pathlib import Path

from backend import runtime_paths


def test_runtime_paths_are_project_local():
    assert runtime_paths.VAR_DIR == runtime_paths.PROJECT_ROOT / "var"
    assert runtime_paths.STATE_FILE == (
        runtime_paths.PROJECT_ROOT / "var" / "state" / "state.json"
    )
    assert runtime_paths.EXECUTION_PLAN_DIR == (
        runtime_paths.PROJECT_ROOT
        / "var"
        / "generated"
        / "execution_plan"
    )


def test_ensure_var_layout_recreates_every_directory(tmp_path):
    var_dir = tmp_path / "var"

    assert not var_dir.exists()

    runtime_paths.ensure_var_layout(var_dir)

    expected = (
        "state",
        "generated",
        "generated/rig",
        "generated/camera_cfg",
        "generated/circumstances",
        "generated/photo_cfg",
        "generated/exposure_opt",
        "generated/sequence",
        "generated/execution_plan",
        "logs",
    )

    for relative in expected:
        assert (var_dir / relative).is_dir()


def test_ensure_var_layout_is_idempotent(tmp_path):
    var_dir = tmp_path / "var"

    runtime_paths.ensure_var_layout(var_dir)
    marker = var_dir / "generated" / "marker.txt"
    marker.write_text("keep", encoding="utf-8")

    runtime_paths.ensure_var_layout(var_dir)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_all_base_persistence_is_under_var():
    persistent_paths = (
        runtime_paths.STATE_FILE,
        runtime_paths.TRIGGER_STATE_FILE,
        runtime_paths.TODAY_ECLIPSE_FILE,
        runtime_paths.LOGS_BUFFER_FILE,
        runtime_paths.RIG_TRACES_FILE,
    )

    var_root = runtime_paths.VAR_DIR.resolve()

    for path in persistent_paths:
        assert path.resolve().is_relative_to(var_root)
