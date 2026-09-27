from tests.frontend_source import read_frontend_source


def test_controls_mount_has_site_time_sync_button_and_route():
    html = read_frontend_source("flask_app/templates/index.html")
    js = read_frontend_source("flask_app/static/js/solartrigger.js")

    assert 'id="btn-mount-sync"' in html
    assert "mountUrl('sync')" in js
    assert "Mount synchronized:" in js
    assert "syncButton.disabled = homing || triggerRunning" in js
