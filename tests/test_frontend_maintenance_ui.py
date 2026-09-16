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
