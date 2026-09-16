from tests.frontend_source import frontend_source


def test_devices_contains_system_and_application_updates():
    source = frontend_source()

    assert "Update System" in source
    assert "Update Solar Eclipse Trigger" in source
    assert 'id="solartrigger-update-file"' in source
    assert "Rollback" in source


def test_add_camera_contains_recharacterization():
    source = frontend_source()

    assert "Re-characterize Camera" in source
    assert "startCameraRecharacterization" in source
    assert "/api/camera-characterization/recharacterize" in source
