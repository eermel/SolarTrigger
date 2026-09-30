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


def test_batch_preflight_route_checks_gps_before_rig_validation():
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
    assert "Do not issue any token until every RIG has passed" in source


def test_normal_trigger_preflights_all_rigs_before_first_start_request():
    source = _function_source("startTrigger", "startDebug")

    preflight = source.index(
        "await preflightTriggerRigs(rigIds, inputs)"
    )
    start_request = source.index("fetch('/api/trigger/start'")

    assert preflight < start_request
    assert "preflight_token: preflightTokens[String(rigId)]" in source


def test_debug_creates_shared_anchor_only_after_all_rigs_preflight():
    source = _function_source("startDebug", "stopTrigger")

    preflight = source.index(
        "await preflightTriggerRigs(rigIds, inputs)"
    )
    anchor = source.index("const debugAnchorUtc = new Date().toISOString();")
    debug_request = source.index("fetch('/api/trigger/debug'")

    assert preflight < anchor < debug_request
    assert "preflight_token: preflightTokens[String(rigId)]" in source


def test_unified_start_uses_one_hardware_preflight_contract_for_all_dates():
    source = _function_source("startTrigger", "startDebug")

    assert "await preflightTriggerRigs(rigIds, inputs)" in source
    assert "fetch('/api/trigger/start'" in source
    assert "preflight_token: preflightTokens[String(rigId)]" in source
    assert "startDryRun" not in JS
    assert "/api/trigger/dryrun" not in JS


def test_debug_backend_carries_preflight_token_into_start_selection():
    start = APP.index(
        '@app.route("/api/trigger/debug", methods=["POST"])'
    )
    end = APP.index("\n@app.route(", start + 1)
    source = APP[start:end]

    assert '"preflight_token": payload.get("preflight_token")' in source
    assert "_start_trigger_with_hardware_preflight(" in source
