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


def test_legacy_preflight_route_still_checks_gps_before_rig_validation():
    start = APP.index(
        '@app.route("/api/trigger/preflight", methods=["POST"])'
    )
    end = APP.index(
        '@app.route("/api/trigger/start", methods=["POST"])',
        start,
    )
    source = APP[start:end]

    gps = source.index("_validate_sequence_gps_first()")
    rig_ids = source.index('raw_rig_ids = payload.get("rig_ids")')
    run = source.index("_run_trigger_hardware_preflight_batch(")
    tokens = source.index("_issue_trigger_preflight_tokens(")

    assert gps < rig_ids < run < tokens


def test_normal_trigger_ui_sends_one_backend_owned_command():
    source = _function_source("startTrigger", "startDebug")

    assert source.count("fetch('/api/trigger/start'") == 1
    assert "rig_ids: rigIds" in source
    assert "preflightTriggerRigs" not in source
    assert "preflight_token" not in source
    assert "for (const rigId of rigIds)" not in source


def test_normal_backend_command_prepares_all_then_preflights_then_launches():
    start = APP.index("def _api_trigger_start_batch(payload):")
    end = APP.index(
        '@app.route("/api/trigger/preflight", methods=["POST"])',
        start,
    )
    source = APP[start:end]

    gps = source.index("_validate_sequence_gps_first()")
    rigs = source.index("_normalize_trigger_rig_ids(")
    prepare = source.index("_prepare_trigger_runtime_circumstances(")
    preflight = source.index("_run_trigger_hardware_preflight_batch(")
    launch = source.index("_launch_preflighted_trigger_batch(")

    assert gps < rigs < prepare < preflight < launch


def test_debug_ui_sends_one_command_without_browser_time_or_tokens():
    source = _function_source("startDebug", "stopTrigger")

    assert source.count("fetch('/api/trigger/debug'") == 1
    assert "rig_ids: rigIds" in source
    assert "debug_anchor_utc" not in source
    assert "new Date().toISOString()" not in source
    assert "preflightTriggerRigs" not in source
    assert "preflight_token" not in source
    assert "for (const rigId of rigIds)" not in source


def test_debug_backend_creates_shared_anchor_after_all_rigs_preflight():
    start = APP.index("def _api_trigger_debug_batch(payload):")
    end = APP.index(
        '@app.route("/api/trigger/debug/clean", methods=["POST"])',
        start,
    )
    source = APP[start:end]

    preflight = source.index("_run_trigger_hardware_preflight_batch(")
    anchor = source.index("now_utc = datetime.now(timezone.utc)")
    generate = source.index("generated = generate_debug_now(now_utc)")
    launch = source.index("_launch_preflighted_trigger_batch(")

    assert preflight < anchor < generate < launch


def test_frontend_contains_no_trigger_preflight_or_launch_fanout_helper():
    assert "async function preflightTriggerRigs" not in JS
    assert "debug_anchor_utc" not in _function_source("startDebug", "stopTrigger")
