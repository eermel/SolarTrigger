import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'flask_app' / 'templates' / 'index.html').read_text(encoding='utf-8')
JS = (ROOT / 'flask_app' / 'static' / 'js' / 'solartrigger.js').read_text(encoding='utf-8')
CSS = (ROOT / 'flask_app' / 'static' / 'css' / 'solartrigger.css').read_text(encoding='utf-8')

def _between(text, start, end):
    match = re.search(re.escape(start) + r'(?P<body>.*?)' + re.escape(end), text, re.DOTALL)
    assert match
    return match.group('body')

MOUNT_JS = _between(JS, '// MOUNT UI START', '// MOUNT UI END')

def test_mount_home_and_virtual_joystick_are_unique():
    assert INDEX.count('id="btn-mount-home"') == 1
    assert INDEX.count('id="mount-joystick"') == 1
    assert INDEX.count('id="mount-joystick-knob"') == 1
    assert INDEX.count('id="mount-joystick-direction"') == 1

def test_old_direction_buttons_and_speed_slider_are_removed():
    assert 'mount-slew-button' not in INDEX
    assert 'id="mount-slew-speed"' not in INDEX
    assert 'class="mount-slew-pad"' not in INDEX

def test_joystick_is_touch_safe_and_has_deadzone():
    assert 'touch-action: none' in CSS
    assert '.mount-joystick-deadzone' in CSS
    assert 'const JOYSTICK_DEADZONE = 0.15;' in MOUNT_JS
    assert 'magnitude < JOYSTICK_DEADZONE' in MOUNT_JS

def test_joystick_maps_eight_directions():
    for label in ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'):
        assert "label: '" + label + "'" in MOUNT_JS
    for pair in ("['north', 'east']", "['south', 'east']", "['south', 'west']", "['north', 'west']"):
        assert pair in MOUNT_JS

def test_joystick_speed_uses_existing_status_capabilities():
    assert 'slewSpeedCaps = data && data.slew_speed_caps;' in MOUNT_JS
    assert 'currentSlewSpeed = data && data.slew_speed;' in MOUNT_JS
    assert "slewSpeedCaps.kind === 'discrete'" in MOUNT_JS
    assert "slewSpeedCaps.kind === 'range'" in MOUNT_JS
    assert '/mount/speed' in MOUNT_JS

def test_joystick_reuses_existing_mount_endpoints_only():
    for endpoint in ('mount/speed', 'mount/slew/start', 'mount/slew/stop'):
        assert endpoint in MOUNT_JS
    assert '/api/mount/' not in MOUNT_JS

def test_diagonal_axes_get_distinct_gesture_ids():
    assert 'motion.directions.forEach(direction => {' in MOUNT_JS
    assert '++slewGestureSequence' in MOUNT_JS
    assert 'gesture_id: gestureId' in MOUNT_JS

def test_release_keeps_redundant_stop_safety_for_every_axis():
    assert 'gesture.slews.forEach(sendSlewStop);' in MOUNT_JS
    assert 'Promise.allSettled(gesture.slews.map(slew => Promise.resolve(slew.startPromise)))' in MOUNT_JS
    assert 'Promise.allSettled(gesture.slews.map(sendSlewStop))' in MOUNT_JS
    assert 'gesture_id: slew.gestureId' in MOUNT_JS

def test_pointer_release_and_page_loss_stop_motion():
    for event in ('pointerup', 'pointercancel', 'lostpointercapture'):
        assert "joystick.addEventListener('" + event + "', releaseJoystick)" in MOUNT_JS
    for event in ('blur', 'pagehide'):
        assert "window.addEventListener('" + event + "', releaseJoystick)" in MOUNT_JS
    assert 'if (document.hidden) releaseJoystick();' in MOUNT_JS

def test_joystick_remains_available_during_trigger_but_not_homing():
    assert 'setJoystickEnabled(!homing && speedAvailable);' in MOUNT_JS
    assert 'setJoystickEnabled(!triggerRunning' not in MOUNT_JS
    assert 'if (homing || !speedAvailable) releaseJoystick();' in MOUNT_JS

def test_tracking_controls_remain_locked_during_trigger():
    assert 'trackingMode.disabled = triggerRunning || modes.length === 0;' in MOUNT_JS
    assert re.search(r'trackingSwitch\.disabled\s*=\s*\(\s*triggerRunning', MOUNT_JS)

def test_refresh_does_not_issue_motion_commands():
    refresh = _between(MOUNT_JS, 'async function refreshMount()', 'function scheduleMountRefresh(delay)')
    assert 'slew/start' not in refresh
    assert 'slew/stop' not in refresh
    assert '/mount/speed' not in refresh

def test_home_preserves_stop_cancel_behavior():
    assert "homeButton.textContent = homing ? 'STOP' : 'HOME';" in MOUNT_JS
    assert "homeButton.classList.toggle('focuser-cancel', homing);" in MOUNT_JS
    assert "mountUrl(homing ? 'slew/stop' : 'home')" in MOUNT_JS
