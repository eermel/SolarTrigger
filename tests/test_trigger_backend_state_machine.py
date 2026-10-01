from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")
JS = (
    ROOT / "flask_app" / "static" / "js" / "solartrigger.js"
).read_text(encoding="utf-8")


def test_backend_exposes_pre_runtime_trigger_state():
    assert 'return "preflighting"' in APP
    assert 'return "starting"' in APP
    assert 'phase = "stopping" if rig_id in cancelled else public_phase' in APP
    assert 'trigger["command"] = {' in APP


def test_start_and_debug_commands_publish_their_backend_mode():
    assert '_begin_trigger_start_command(rig_ids, "real")' in APP
    assert '_begin_trigger_start_command(rig_ids, "debug")' in APP
    assert '"mode": str(mode)' in APP


def test_all_trigger_status_surfaces_use_authoritative_snapshot():
    assert (
        'def _authoritative_trigger_snapshot():\n'
        '    return _trigger_status_snapshot(sync_runtime=True)'
    ) in APP
    assert (
        '@app.route("/api/trigger/status")\n'
        'def api_trigger_status():\n'
        '    return jsonify(_trigger_status_snapshot(sync_runtime=True))'
    ) in APP
    assert 'trigger = _trigger_status_snapshot(sync_runtime=False)' in APP


def test_ui_has_labels_for_backend_pre_runtime_phases():
    assert "preflighting: '⏳ PRE-FLIGHT'" in JS
    assert "starting:     '▶ STARTING'" in JS
    assert "stopping:     '■ STOPPING'" in JS


def test_stop_ui_does_not_invent_trigger_state():
    start = JS.index("async function stopTrigger()")
    end = JS.index("\nasync function startTotalityOnly()", start)
    source = JS[start:end]

    assert "state.triggerRigs[rigKey] =" not in source
    assert "await refreshTriggerStatusFromBackend();" in source
    assert "fetch('/api/trigger/status')" in JS


def test_debug_clean_safety_interlock_is_backend_owned():
    route_start = APP.index(
        '@app.route("/api/trigger/debug/clean", methods=["POST"])'
    )
    route_end = APP.index(
        '@app.route("/api/trigger/debug", methods=["POST"])',
        route_start + 1,
    )
    route = APP[route_start:route_end]

    assert "_trigger_command_snapshot() is not None" in route
    assert "_trigger_service.any_active_or_starting()" in route
    assert '"code": "TRIGGER_ACTIVE"' in route

    clean_start = JS.index("async function cleanDebugGeneratedFiles()")
    clean_end = JS.index("\n\nfunction _observeDebugMirror", clean_start)
    clean = JS[clean_start:clean_end]
    assert "anyActiveTriggerRunning()" not in clean
