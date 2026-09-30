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


def test_debug_tab_and_panel_are_removed_from_ui():
    assert 'id="debug-tab"' not in HTML
    assert '<span>DEBUG</span>' not in HTML
    assert 'id="debug-panel"' not in HTML
    assert 'onclick="showTab(10)"' not in HTML


def test_trigger_contains_debug_clean_and_unified_start_actions():
    trigger = _panel("page-4")

    assert 'id="btn-debug"' in trigger
    assert 'id="btn-debug-clean"' in trigger
    assert 'id="btn-dryrun"' not in trigger
    assert 'id="btn-start"' in trigger
    assert 'id="btn-stop"' in trigger
    assert 'id="btn-totality-only"' in trigger
    assert 'onclick="startDebug()"' in trigger
    assert 'onclick="cleanDebugGeneratedFiles()"' in trigger


def test_debug_and_clean_share_half_width_row_above_start():
    trigger = _panel("page-4")

    row = trigger.index('class="trigger-debug-actions"')
    debug = trigger.index('id="btn-debug"', row)
    clean = trigger.index('id="btn-debug-clean"', row)
    start = trigger.index('id="btn-start"', row)

    assert row < debug < start
    assert row < clean < start

    assert ".trigger-debug-actions {" in CSS
    block = CSS.split(".trigger-debug-actions {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in block
    assert "width: 100%;" in block


def test_removed_debug_panel_has_no_duplicate_controls():
    for identifier in (
        "debug-circumstances-select",
        "debug-photo-select",
        "debug-exposure-opt-select",
        "debug-contacts",
        "debug-target-label",
        "debug-log-title",
        "log-container-debug",
        "btn-debug-start",
        "btn-debug-totality-only",
        "btn-debug-stop",
    ):
        assert f'id="{identifier}"' not in HTML


def test_debug_clean_button_keeps_secondary_style():
    trigger = _panel("page-4")
    clean_match = re.search(
        r'<button[^>]*class="([^"]*)"[^>]*id="btn-debug-clean"',
        trigger,
        re.DOTALL,
    )
    assert clean_match
    assert "btn-secondary" in clean_match.group(1)


def test_trigger_debug_button_uses_existing_debug_engine_directly():
    trigger = _panel("page-4")

    assert 'onclick="startDebug()"' in trigger
    assert "async function startDebug()" in INDEX
    assert "fetch('/api/trigger/debug'" in INDEX
    assert "async function cleanDebugGeneratedFiles()" in INDEX


def test_debug_clean_endpoint_only_targets_generated_debug_files():
    route_start = APP.index(
        '@app.route("/api/trigger/debug/clean", methods=["POST"])'
    )
    route_end = APP.index(
        '@app.route("/api/trigger/debug", methods=["POST"])',
        route_start,
    )
    source = APP[route_start:route_end]

    assert 'glob("debug_rig_*.json")' in source
    assert 'glob("*.json")' not in source
    assert "path.is_symlink()" in source
    assert "path.unlink()" in source


def test_debug_navigation_is_removed():
    show_start = INDEX.index("function showTab(n)")
    show_end = INDEX.index("\n}\n", show_start) + 2
    show_tab = INDEX[show_start:show_end]

    assert "'debug-panel'" not in show_tab
    assert "n === 10" not in show_tab


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


def test_debug_countdown_mirror_does_not_rebuild_on_live_ticks():
    assert "debugContacts.innerHTML = contacts.innerHTML;" not in INDEX
    assert "mirror.querySelectorAll('[id]')" in INDEX
    assert "node.id = `debug-${node.id}`;" in INDEX
    assert "['cd-', 'td-', 'debug-td-']" in INDEX
    assert "[k, `debug-${k}`].forEach(rowId => {" in INDEX

    install_start = INDEX.index("function installDebugUiMirror()")
    install_end = INDEX.index("\n}\n", install_start) + 2
    install = INDEX[install_start:install_end]

    assert (
        "_observeDebugMirror(\n"
        "    'trigger-contacts',\n"
        "    syncDebugCircumstances,\n"
        "    {subtree: false, characterData: false, attributes: false}\n"
        "  );"
    ) in install


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

    trigger_start = CSS.index(".trigger-action-stack > .trigger-action-button,")
    trigger_end = CSS.index("}", trigger_start)
    trigger_button = CSS[trigger_start:trigger_end]

    expected = "height: calc(var(--btn-h) * 1.5);"
    expected_min = "min-height: calc(var(--btn-h) * 1.5);"

    assert expected in cam_button
    assert expected_min in cam_button
    assert expected in trigger_button
    assert expected_min in trigger_button


def test_circumstances_rerender_refreshes_countdown_without_placeholder_flash():
    render_start = INDEX.index("function renderContacts(data)")
    render_end = INDEX.index("\nfunction updateCountdowns(data)", render_start)
    render = INDEX[render_start:render_end]

    rebuild = "tlist.innerHTML = _buildContactsHtml();"
    refresh = "updateCountdowns(data);"

    assert rebuild in render
    assert refresh in render
    assert render.index(rebuild) < render.index(refresh)


def test_debug_loads_diamond_duration_before_backend_can_render_generated_contacts():
    start = INDEX.index("async function startDebug()")
    end = INDEX.index("\nasync function stopTrigger()", start)
    source = INDEX[start:end]

    load = "await loadTriggerDiamondDuration(inputs.photo_file);"
    request = "fetch('/api/trigger/debug'"

    assert load in source
    assert request in source
    assert source.index(load) < source.index(request)


def test_diamond_duration_reload_has_no_transient_null_before_await():
    start = INDEX.index("async function loadTriggerDiamondDuration(")
    end = INDEX.index("\nasync function refreshTriggerCircumstancesForPhoto()", start)
    source = INDEX[start:end]

    first_fetch = source.index("await fetch(")
    filename_guard_end = source.index("  // Do not clear", 0, first_fetch)

    # Clearing is legitimate only when there is no Photo Setup filename.
    # Once a real file is selected, the previous valid duration must survive
    # until the asynchronous read has completed.
    assert "state.triggerDiamondDurationS = null;" in source[:filename_guard_end]
    assert "state.triggerDiamondDurationS = null;" not in source[filename_guard_end:first_fetch]
    assert "let nextDuration = null;" in source
    assert "state.triggerDiamondDurationS = nextDuration;" in source


def test_debug_circumstances_embed_authoritative_diamond_duration():
    route_start = APP.index(
        '@app.route("/api/trigger/debug", methods=["POST"])'
    )
    route_end = APP.index(
        '\n@app.route(',
        route_start + 1,
    )
    route = APP[route_start:route_end]

    assert 'debug_photo_setup = json.load(handle)' in route
    assert 'generated["_diamond_ring_duration_s"] = diamond_duration' in route


def test_trigger_and_debug_use_embedded_diamond_duration_before_ui_state():
    assert "function _triggerDiamondDurationFor(data)" in INDEX
    assert "data._diamond_ring_duration_s" in INDEX

    render_start = INDEX.index("function renderContacts(data)")
    render_end = INDEX.index("\nfunction updateCountdowns(data)", render_start)
    render = INDEX[render_start:render_end]
    assert "const diamondDuration = _triggerDiamondDurationFor(data);" in render

    countdown_start = INDEX.index("function updateCountdowns(data)")
    countdown_end = INDEX.index("\nfunction fmt(", countdown_start)
    countdown = INDEX[countdown_start:countdown_end]
    assert "const diamondDuration = _triggerDiamondDurationFor(data);" in countdown
