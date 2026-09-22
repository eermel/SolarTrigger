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


def test_debug_stop_state_tracks_trigger_stop_attribute_changes():
    assert "_observeDebugMirror('btn-stop', syncDebugActionState);" in INDEX
    assert "window._triggerStopPendingRigs" in INDEX
    assert "btn-debug-stop" in INDEX


def test_debug_log_mirror_does_not_rewrite_log_on_unrelated_ui_mutations():
    install_start = INDEX.index("function installDebugUiMirror()")
    install_end = INDEX.index("\n}\n", install_start) + 2
    install = INDEX[install_start:install_end]

    assert "_observeDebugMirror(" in install
    assert "'log-container-trigger'," in install
    assert "syncDebugLog," in install
    assert "_observeDebugMirror('btn-stop', syncDebugActionState);" in install

    broad_callback = (
        "syncDebugCircumstances();\n"
        "    syncDebugRigSelection();\n"
        "    syncDebugLog();\n"
        "    syncDebugActionState();"
    )
    assert broad_callback not in install


def test_trigger_and_debug_logs_preserve_manual_scroll_position():
    assert "function _logNearBottom(container, thresholdPx = 32)" in INDEX

    append_start = INDEX.index("function appendTriggerRigLog(entry)")
    append_end = INDEX.index("\n}\n", append_start) + 2
    append = INDEX[append_start:append_end]
    assert "const followTail = _logNearBottom(container);" in append
    assert "if (followTail)" in append

    mirror_start = INDEX.index("function syncDebugLog()")
    mirror_end = INDEX.index("\n}\n", mirror_start) + 2
    mirror = INDEX[mirror_start:mirror_end]
    assert "const followTail = _logNearBottom(target);" in mirror
    assert "const previousScrollTop = target.scrollTop;" in mirror
    assert "target.scrollTop = Math.min(" in mirror


def test_active_runtime_inputs_restore_photo_setup_and_diamond_duration():
    assert "async function restoreActiveTriggerInputs()" in INDEX
    assert "rigState.inputs" in INDEX
    assert "['photo_file', 'trigger-photo-select']" in INDEX
    assert "['exposure_opt_file', 'trigger-exposure-opt-select']" in INDEX
    assert "await loadTriggerDiamondDuration();" in INDEX
    assert "state.triggerCircumstances || state.eclipse" in INDEX
    assert "await restoreActiveTriggerInputs();" in INDEX


def test_reconnect_restores_runtime_inputs_and_rig_devices():
    restore_start = INDEX.index("async function restoreActiveTriggerInputs()")
    restore_end = INDEX.index("\nasync function loadTriggerCircumstances", restore_start)
    restore = INDEX[restore_start:restore_end]

    assert "allAvailable" not in restore
    assert "_selectRuntimeActiveTriggerInput(selectId, filename)" in restore
    assert "await loadTriggerDiamondDuration();" in restore
    assert "option.dataset.runtimeActive = 'true';" in INDEX

    connect_start = INDEX.index("socket.on('connect', async () => {")
    connect_end = INDEX.index("\n});", connect_start) + 4
    connect = INDEX[connect_start:connect_end]
    assert "await loadRigDevices();" in connect

    assert (
        "const byId = new Map(\n"
        "    rigDevicesState.rigs.map(rig => [Number(rig.rig_id), rig])\n"
        "  );"
    ) in INDEX


def test_system_tab_uses_settings_icon_and_hides_empty_release_prompt():
    system_start = HTML.index('id="add-camera-tab"')
    system_start = HTML.rfind("<button", 0, system_start)
    system_end = HTML.index("</button>", system_start)
    system_button = HTML[system_start:system_end]

    assert '<circle cx="12" cy="12" r="3"/>' in system_button
    assert "M20 13v6H3V7" not in system_button

    assert "Select an offline release ZIP." not in HTML
    assert '<div id="solartrigger-update-status"></div>' in HTML
    assert "#solartrigger-update-status:empty" in CSS


def test_camera_buttons_match_trigger_start_height():
    large_start = CSS.index("#btn-focuser-home,")
    large_end = CSS.index("}", large_start)
    large_controls = CSS[large_start:large_end]

    assert ".cam-rig-button" not in large_controls

    cam_start = CSS.index(".cam-rig-button {")
    cam_end = CSS.index("}", cam_start)
    cam_button = CSS[cam_start:cam_end]

    trigger_start = CSS.index(".trigger-action-stack > .trigger-action-button {")
    trigger_end = CSS.index("}", trigger_start)
    trigger_button = CSS[trigger_start:trigger_end]

    expected = "height: calc(var(--btn-h) * 1.5);"
    expected_min = "min-height: calc(var(--btn-h) * 1.5);"

    assert expected in cam_button
    assert expected_min in cam_button
    assert expected in trigger_button
    assert expected_min in trigger_button
