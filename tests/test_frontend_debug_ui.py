from pathlib import Path
import re

from tests.frontend_source import frontend_source


ROOT = Path(__file__).resolve().parents[1]
INDEX = frontend_source()
HTML = (
    ROOT / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")
CSS = (
    ROOT / "flask_app" / "static" / "css" / "solartrigger.css"
).read_text(encoding="utf-8")
APP = (
    ROOT / "flask_app" / "app.py"
).read_text(encoding="utf-8")


def _panel(panel_id):
    start = HTML.index(f'<div class="page" id="{panel_id}">')
    end = HTML.index('</div><!-- /', start)
    return HTML[start:end]


def test_debug_tab_follows_trigger_and_is_outside_numbered_workflow():
    trigger_pos = HTML.index("<span>TRIGGER</span>")
    debug_pos = HTML.index("<span>DEBUG</span>")

    assert trigger_pos < debug_pos
    assert 'id="debug-tab"' in HTML
    assert 'data-page-index="10"' in HTML
    assert 'onclick="showTab(10)"' in HTML

    debug_button_start = HTML.index('id="debug-tab"')
    debug_button_start = HTML.rfind("<button", 0, debug_button_start)
    debug_button_end = HTML.index("</button>", debug_button_start)
    debug_tab = HTML[debug_button_start:debug_button_end]

    assert "data-workflow-step" not in debug_tab
    assert "workflow-step-number" not in debug_tab

    trigger_button_start = HTML.rfind("<button", 0, trigger_pos)
    trigger_button_end = HTML.index("</button>", trigger_button_start)
    trigger_tab = HTML[trigger_button_start:trigger_button_end]

    assert "data-workflow-last" in trigger_tab


def test_trigger_no_longer_contains_debug_or_dryrun_actions():
    trigger = _panel("page-4")

    assert 'id="btn-debug"' not in trigger
    assert 'id="btn-dryrun"' not in trigger
    assert 'id="btn-start"' in trigger
    assert 'id="btn-stop"' in trigger
    assert 'id="btn-totality-only"' in trigger


def test_debug_panel_contains_test_actions_and_white_clean_button():
    debug = _panel("debug-panel")

    assert "DEBUG / TEST MODE" in debug
    assert 'id="btn-debug"' in debug
    assert 'id="btn-dryrun"' in debug
    assert 'id="btn-debug-start"' in debug
    assert 'id="btn-debug-totality-only"' in debug
    assert 'id="btn-debug-stop"' in debug
    assert 'id="btn-debug-clean"' in debug

    clean_match = re.search(
        r'<button[^>]*class="([^"]*)"[^>]*id="btn-debug-clean"',
        debug,
        re.DOTALL,
    )
    assert clean_match
    assert "btn-secondary" in clean_match.group(1)


def test_debug_panel_duplicates_trigger_configuration_rig_and_log_ui():
    debug = _panel("debug-panel")

    for identifier in (
        "debug-circumstances-select",
        "debug-photo-select",
        "debug-exposure-opt-select",
        "debug-contacts",
        "debug-target-label",
        "debug-log-title",
        "log-container-debug",
    ):
        assert f'id="{identifier}"' in debug

    for rig_id in range(1, 5):
        assert f'id="debug-rig-{rig_id}"' in debug
        assert f'onclick="selectDebugTriggerRig({rig_id})"' in debug


def test_debug_banner_is_yellow_and_visually_distinct():
    assert ".debug-mode-banner {" in CSS
    block = CSS.split(".debug-mode-banner {", 1)[1].split("}", 1)[0]
    assert "var(--yellow)" in block


def test_debug_frontend_reuses_existing_trigger_functions():
    for function_name in (
        "startDebugFromDebugTab",
        "startDryRunFromDebugTab",
        "startTriggerFromDebugTab",
        "startTotalityOnlyFromDebugTab",
        "stopTriggerFromDebugTab",
        "syncDebugUiFromTrigger",
        "syncTriggerInputsFromDebug",
        "cleanDebugGeneratedFiles",
    ):
        assert f"function {function_name}" in INDEX

    assert "await startDebug();" in INDEX
    assert "await startDryRun();" in INDEX
    assert "await startTrigger();" in INDEX
    assert "await startTotalityOnly();" in INDEX
    assert "await stopTrigger();" in INDEX
    assert "selectTriggerRig(rigId);" in INDEX


def test_debug_clean_endpoint_only_targets_generated_debug_files():
    route_start = APP.index(
        '@app.route("/api/trigger/debug/clean", methods=["POST"])'
    )
    route_end = APP.index(
        '@app.route("/api/trigger/dryrun", methods=["POST"])',
        route_start,
    )
    source = APP[route_start:route_end]

    assert 'glob("debug_rig_*.json")' in source
    assert 'glob("*.json")' not in source
    assert "path.is_symlink()" in source
    assert "path.unlink()" in source


def test_debug_navigation_is_registered():
    assert "'debug-panel'" in INDEX
    assert re.search(
        r"if\s*\(\s*n\s*===\s*10\s*\)",
        INDEX,
    )


def test_debug_log_uses_same_visual_container_as_other_logs():
    assert re.search(
        r"#log-container-trigger,\s*"
        r"#log-container-debug,\s*"
        r"#camera-add-log,",
        CSS,
    )


def test_trigger_and_debug_circumstances_display_whole_seconds_only():
    assert "const _triggerDisplayHms = value =>" in INDEX
    assert r"(?:\.\d+)?" in INDEX
    assert "${_triggerDisplayHms(c.utc)}" in INDEX
    assert "${_triggerDisplayHms(c.local)}" in INDEX


def test_diamond_ring_trigger_label_cannot_wrap():
    assert 'class="trigger-contact-label"' in INDEX
    block = CSS.split(".trigger-contact-label {", 1)[1].split("}", 1)[0]
    assert "white-space: nowrap" in block
    assert "min-width: 100px" in block
