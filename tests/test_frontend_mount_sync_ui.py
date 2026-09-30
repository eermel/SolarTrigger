from tests.frontend_source import frontend_source


def test_controls_mount_has_site_time_sync_button_and_route():
    source = frontend_source()

    assert 'id="btn-mount-sync"' in source
    assert "mountUrl('sync')" in source
    assert "Mount synchronized:" in source
    assert "syncButton.disabled = homing || triggerRunning" in source


def test_mount_sync_button_matches_mount_home_touch_height():
    source = frontend_source()

    assert "#btn-mount-home," in source
    assert "#btn-mount-sync," in source
    assert "height: calc(var(--btn-h) * 2);" in source
    assert "min-height: calc(var(--btn-h) * 2);" in source
