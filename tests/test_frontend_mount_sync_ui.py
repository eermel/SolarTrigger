from tests.frontend_source import frontend_source


def test_controls_mount_has_site_time_sync_button_and_route():
    source = frontend_source()

    assert 'id="btn-mount-sync"' in source
    assert "mountUrl('sync')" in source
    assert "Mount synchronized:" in source
    assert "syncButton.disabled = homing || triggerRunning" in source
