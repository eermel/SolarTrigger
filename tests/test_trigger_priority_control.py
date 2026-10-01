from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")
CHAR = (
    ROOT / "backend" / "camera_characterization_routes.py"
).read_text(encoding="utf-8")
VALID = (
    ROOT / "backend" / "camera_validation_routes.py"
).read_text(encoding="utf-8")


def _route(source, marker, next_marker):
    start = source.index(marker)
    end = source.index(next_marker, start)
    return source[start:end]


def test_stop_cancels_pending_backend_start_before_runtime_stop():
    source = _route(
        APP,
        '@app.route("/api/trigger/stop", methods=["POST"])',
        '@app.route("/api/trigger/status")',
    )
    cancel = source.index("_trigger_commands.cancel_rig(rig_id)")
    runtime = source.index("_trigger_service.stop(")
    assert cancel < runtime
    assert '"cancelled_start"' in source


def test_emergency_uses_priority_admission_not_normal_start_guard():
    source = _route(
        APP,
        '@app.route("/api/trigger/totality_only", methods=["POST"])',
        "def _emit_trigger",
    )
    assert "_trigger_start_guarded" not in source
    assert "_trigger_commands.begin_emergency()" in source
    assert "_wait_for_emergency_competitors(" in source
    assert "trigger_priority_section(" in source
    assert "_trigger_service.start_totality_only(" in source


def test_camera_maintenance_guards_always_allow_priority_trigger_routes():
    expected = (
        'if path in {"/api/trigger/stop", "/api/trigger/totality_only"}:'
    )
    assert expected in CHAR
    assert expected in VALID


def test_system_maintenance_sees_pending_backend_commands_as_trigger_busy():
    marker = "register_system_maintenance_routes("
    source = APP[APP.index(marker):]
    assert "_trigger_commands.busy()" in source
