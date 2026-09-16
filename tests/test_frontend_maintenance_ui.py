from tests.frontend_source import frontend_source


def test_system_tab_contains_camera_and_maintenance_tools():
    source = frontend_source()

    assert "<span>SYSTEM</span>" in source
    assert "<span>ADD CAMERA</span>" not in source

    assert "Camera Characterization" in source
    assert "Re-characterize Camera" in source
    assert "Camera Validation" in source

    assert "Persistent data" in source
    assert "Update System" in source
    assert "Update Solar Eclipse Trigger" in source


def test_system_update_is_single_full_width_action():
    source = frontend_source()

    assert 'id="system-check-update"' in source
    assert "CHECK AND UPDATE" in source
    assert "checkAndUpdateSystem()" in source
    assert "onclick=\"checkSystemUpdates()\"" not in source
    assert "onclick=\"updateSystem()\"" not in source
    assert "/api/system/maintenance/update-system" in source


def test_application_update_actions_share_one_row():
    source = frontend_source()

    assert 'class="system-release-actions"' in source
    assert "Validate package" in source
    assert "Install update" in source
    assert "Rollback" in source
    assert 'id="solartrigger-update-file"' in source


def test_system_maintenance_cards_have_requested_colours():
    source = frontend_source()

    assert "system-maintenance-card--orange" in source
    assert "system-maintenance-card--blue" in source


def test_add_camera_contains_recharacterization():
    source = frontend_source()

    assert "Re-characterize Camera" in source
    assert "startCameraRecharacterization" in source
    assert "/api/camera-characterization/recharacterize" in source



def test_system_page_uses_one_shared_log():
    source = frontend_source()

    assert "<span>Camera log</span>" not in source
    assert "<span>Log</span>" in source

    assert 'id="camera-add-log"' in source
    assert 'id="system-update-log"' not in source
    assert 'id="solartrigger-update-log"' not in source


def test_maintenance_uses_shared_system_log():
    source = frontend_source()

    assert "updateCameraAddLog('systemUpdate', lines)" in source
    assert "updateCameraAddLog('solarTriggerUpdate', lines)" in source

    assert "=== UPDATE SYSTEM ===" in source
    assert "=== UPDATE SOLAR ECLIPSE TRIGGER ===" in source

    assert "getElementById('system-update-log')" not in source
    assert "getElementById('solartrigger-update-log')" not in source


def test_clear_system_log_includes_maintenance():
    source = frontend_source()

    assert "cameraAddLogState.systemUpdateOffset =" in source
    assert "cameraAddLogState.solarTriggerUpdateOffset =" in source



def test_system_update_button_tracks_ethernet_link():
    source = frontend_source()

    assert "CHECK AND UPDATE SYSTEM" in source
    assert "Eth need to be connected" in source
    assert "button.disabled = !ethernetConnected" in source
    assert "data.ethernet && data.ethernet.connected" in source


def test_system_update_has_no_network_status_line():
    source = frontend_source()

    assert 'id="system-update-network"' not in source
    assert "Ethernet: checking" not in source


def test_system_update_has_no_apt_explanatory_text():
    source = frontend_source()

    assert "Physical Ethernet required." not in source



def test_system_camera_group_contains_camera_workflow():
    source = frontend_source()

    assert 'class="system-camera-group"' in source
    assert 'class="system-camera-group-title">Camera</div>' in source

    group_start = source.index('class="system-camera-group"')
    persistent_start = source.index(
        '<div class="card-title">Persistent data</div>'
    )

    camera_group = source[group_start:persistent_start]

    expected = [
        '<div class="card-title">Device discovery</div>',
        '<div class="card-title">Camera Characterization</div>',
        '<div class="card-title">Re-characterize Camera</div>',
        '<div class="card-title">Camera Validation</div>',
    ]

    positions = [camera_group.index(item) for item in expected]

    assert positions == sorted(positions)


def test_recharacterization_select_has_chevron():
    source = frontend_source()

    marker = 'id="camera-recharacterization-select"'
    start = source.index(marker)
    select = source[start:source.index(">", start)]

    assert "file-select-chevron" in select
