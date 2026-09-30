from pathlib import Path

from tests.frontend_source import frontend_source


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")
HTML = (ROOT / "flask_app" / "templates" / "index.html").read_text(encoding="utf-8")
UI = frontend_source()


def _slice(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_start_and_dryrun_are_one_cosmetic_button():
    assert 'id="btn-start"' in HTML
    assert 'id="btn-dryrun"' not in HTML
    assert "startDryRun" not in UI
    assert "/api/trigger/dryrun" not in UI
    assert "/api/trigger/dryrun" not in APP
    assert "const label = isRealDate ? '▶ START' : '🧪 DRY-RUN';" in UI


def test_button_mode_uses_pi_authoritative_utc_clock():
    block = _slice(
        UI,
        "function currentUtcDateIso()",
        "function selectedEclipseDateIso()",
    )
    assert "_clockAnchorUtcMs" in block
    assert "_clockAnchorPerfMs" in block
    assert "_nowAdjustedUtcMs()" in block
    assert "new Date().toISOString()" not in block


def test_normal_start_only_prepares_date_then_calls_shared_engine_handoff():
    prepare = _slice(
        APP,
        "def _prepare_trigger_runtime_circumstances",
        "def _start_trigger_with_hardware_preflight",
    )
    route = _slice(
        APP,
        '@app.route("/api/trigger/start"',
        '@app.route("/api/trigger/simulate"',
    )

    assert "generate_dryrun_today(source, now_utc)" in prepare
    assert 'prepared["_generated_utc"] = now_utc.isoformat()' in prepare
    assert 'effective["circumstances_file"] = filename' in prepare
    assert "_prepare_trigger_runtime_circumstances" in route
    assert "_start_trigger_with_hardware_preflight" in route
    assert "dry_run=True" not in route


def test_debug_generates_inputs_then_calls_same_engine_handoff():
    route = _slice(
        APP,
        "def api_trigger_debug():",
        '@app.route("/api/trigger/stop"',
    )

    assert "generate_debug_now(now_utc)" in route
    assert "_start_trigger_with_hardware_preflight(" in route
    assert "_trigger_service.start(" not in route
    assert "dry_run=True" not in route


def test_shared_handoff_is_the_only_hardware_engine_call_for_prepared_inputs():
    helper = _slice(
        APP,
        "def _start_trigger_with_hardware_preflight",
        '@app.route("/api/trigger/preflight"',
    )

    assert "_trigger_service.start(" in helper
    assert "simulate=False" in helper
    assert "dry_run=" not in helper
