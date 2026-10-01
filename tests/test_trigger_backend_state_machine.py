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
