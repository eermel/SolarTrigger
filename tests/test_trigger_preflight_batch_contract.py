from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")
JS = (
    ROOT / "flask_app" / "static" / "js" / "solartrigger.js"
).read_text(encoding="utf-8")


def _function_source(name, next_name):
    start = JS.index(f"async function {name}()")
    end = JS.index(f"\nasync function {next_name}()", start)
    return JS[start:end]


def _route_source(route, next_route):
    start = APP.index(route)
    end = APP.index(next_route, start + 1)
    return APP[start:end]


def test_legacy_preflight_route_and_tokens_are_removed():
    assert '/api/trigger/preflight' not in APP
    assert "preflight_token" not in APP
    assert "_trigger_preflight_tokens" not in APP
    assert "_issue_trigger_preflight_tokens" not in APP
    assert "_consume_trigger_preflight_token" not in APP


def test_normal_trigger_ui_submits_one_backend_command_without_orchestration():
    source = _function_source("startTrigger", "startDebug")

    assert source.count("fetch('/api/trigger/start'") == 1
    assert "rig_ids: rigIds" in source
    assert "preflightTriggerRigs" not in source
    assert "preflight_token" not in source
    assert "for (const rigId of rigIds)" not in source


def test_debug_ui_submits_one_backend_command_without_time_orchestration():
    source = _function_source("startDebug", "stopTrigger")

    assert source.count("fetch('/api/trigger/debug'") == 1
    assert "rig_ids: rigIds" in source
    assert "preflightTriggerRigs" not in source
    assert "preflight_token" not in source
    assert "debugAnchorUtc" not in source
    assert "debug_anchor_utc" not in source
    assert "new Date().toISOString()" not in source
    assert "for (const rigId of rigIds)" not in source


def test_normal_start_backend_preflights_all_rigs_before_first_engine_handoff():
    source = _route_source(
        '@app.route("/api/trigger/start", methods=["POST"])',
        '@app.route("/api/trigger/simulate", methods=["POST"])',
    )

    gps = source.index("_validate_sequence_gps_first()")
    rig_ids = source.index("_trigger_rig_ids_from_payload(payload)")
    prepare = source.index("for rig_id in rig_ids:")
    preflight = source.index("_run_trigger_hardware_preflight_batch(")
    launch = source.index("_start_preflighted_trigger(")

    assert gps < rig_ids < prepare < preflight < launch
    assert "hardware_preflight_done" not in source


def test_debug_backend_owns_preflight_and_shared_utc_anchor():
    source = _route_source(
        '@app.route("/api/trigger/debug", methods=["POST"])',
        '@app.route("/api/trigger/stop", methods=["POST"])',
    )

    preflight = source.index("_run_trigger_hardware_preflight_batch(")
    anchor = source.index("now_utc = datetime.now(timezone.utc)")
    generate = source.index("generate_debug_now(now_utc)")
    launch = source.index("_start_preflighted_trigger(")

    assert preflight < anchor < generate < launch
    assert "hardware_preflight_done=True" in source
    assert "debug_anchor_utc" not in source
    assert "preflight_token" not in source


def test_unified_start_keeps_one_hardware_engine_for_all_dates():
    normal = _function_source("startTrigger", "startDebug")

    assert "fetch('/api/trigger/start'" in normal
    assert "startDryRun" not in JS
    assert "/api/trigger/dryrun" not in JS



def test_internal_trigger_contract_has_no_completed_p0_compatibility_shims():
    assert "P0-C will fold" not in APP
    assert 'def _begin_trigger_start_command(rig_ids, mode="real")' not in APP
    assert "hardware_preflight_done" not in APP
    assert "_start_trigger_with_hardware_preflight" not in APP
    assert "def _start_preflighted_trigger(*, rig_id, selected=None):" in APP
