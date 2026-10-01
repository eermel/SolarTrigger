from tests.frontend_source import frontend_source


UI = frontend_source()


def test_trigger_global_actions_submit_one_backend_command_for_active_rigs():
    assert "function activeTriggerRigIds()" in UI

    start = UI.index("async function startTrigger()")
    debug = UI.index("async function startDebug()", start)
    stop = UI.index("async function stopTrigger()", debug)
    start_source = UI[start:debug]
    debug_source = UI[debug:stop]

    assert start_source.count("fetch('/api/trigger/start'") == 1
    assert "rig_ids: rigIds" in start_source
    assert "for (const rigId of rigIds)" not in start_source

    assert "async function startDryRun()" not in UI
    assert "'/api/trigger/dryrun'" not in UI

    assert debug_source.count("fetch('/api/trigger/debug'") == 1
    assert "rig_ids: rigIds" in debug_source
    assert "for (const rigId of rigIds)" not in debug_source


def test_trigger_global_action_labels_keep_debug_all_and_date_driven_start():
    assert "'🧪 DEBUG ALL'" in UI
    assert "const label = isRealDate ? '▶ START' : '🧪 DRY-RUN';" in UI
    assert "'🧪 DRY-RUN ALL'" not in UI


def test_stop_and_totality_override_remain_selected_rig_actions():
    assert "async function stopTrigger()" in UI
    assert "body: JSON.stringify({rig_id: selectedTriggerRigId})" in UI

    assert "async function startTotalityOnly()" in UI
    assert "'/api/trigger/totality_only'" in UI


def test_global_start_actions_lock_when_any_active_rig_runs():
    assert "function anyActiveTriggerRunning()" in UI
    assert "const triggerStartLocked = anyActiveTriggerRunning();" in UI
    assert "if (btnStart) btnStart.disabled = triggerStartLocked;" in UI
    assert "btnDryRun" not in UI
    assert "if (btnDebug) btnDebug.disabled = triggerStartLocked;" in UI


def test_trigger_log_is_single_panel_for_selected_rig():
    assert UI.count('id="log-container-trigger"') == 1

    for rig_id in range(1, 5):
        assert f'id="log-container-trigger-rig-{rig_id}"' not in UI

    assert 'id="trigger-log-title"' in UI
    assert "const triggerLogEntries = {" in UI
    assert "function renderTriggerLog()" in UI
    assert "function appendTriggerRigLog(entry)" in UI
    assert "triggerLogEntries[rigId].push(entry);" in UI
    assert "`Trigger log — RIG ${selectedTriggerRigId}`" in UI
    assert "`[${timestamp}][RIG${rigId}][${icon}] ${entry.text || ''}`" in UI


def test_trigger_log_icons_cover_astronomical_phases():
    assert "warning: '☀️'" in UI
    assert "orange: '🌙'" in UI
    assert "purple: '💍'" in UI
    assert "totality: '🌑'" in UI


def test_trigger_log_icon_is_driven_by_astronomical_time():
    assert "function triggerAstronomicalIcon(timestamp)" in UI
    assert "state.triggerCircumstances" in UI
    assert "state.triggerDiamondDurationS" in UI

    assert "if (event < c1) return '☀️';" in UI
    assert "if (event < diamondBefore) return '🌙';" in UI
    assert "if (event < c2) return '💍';" in UI
    assert "if (event < c3) return '🌑';" in UI
    assert "if (event < diamondAfter) return '💍';" in UI
    assert "if (event < c4) return '🌙';" in UI

    assert (
        "triggerAstronomicalIcon(entry.timestamp) ||"
        in UI
    )
