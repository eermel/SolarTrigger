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

def test_virtual_joystick_has_operator_readout_and_cardinal_labels():
    assert 'id="mount-joystick-direction"' in INDEX
    assert 'id="mount-slew-speed-value"' in INDEX
    for label in ('N', 'E', 'S', 'W'):
        assert 'mount-joystick-label-' + label.lower() in INDEX

def test_pointer_motion_is_clamped_to_circular_pad():
    assert 'const distance = Math.hypot(dx, dy);' in MOUNT_JS
    assert 'const magnitude = Math.min(1, distance / radius);' in MOUNT_JS
    assert 'if (distance > radius)' in MOUNT_JS
    assert 'radius / distance' in MOUNT_JS

def test_deadzone_requests_stop_instead_of_slew():
    assert re.search(r'if \(magnitude < JOYSTICK_DEADZONE\).*?requestMotion\(null\)', MOUNT_JS, re.DOTALL)

def test_elongation_selects_discrete_or_range_speed():
    assert 'function speedForMagnitude(magnitude)' in MOUNT_JS
    assert 'Math.floor(normalized * slewSpeedCaps.values.length)' in MOUNT_JS
    assert 'Math.round((raw - minimum) / step) * step' in MOUNT_JS

def test_motion_state_changes_only_when_direction_or_speed_changes():
    assert 'if (motionKey(motion) === motionKey(desiredMotion)) return;' in MOUNT_JS
    assert "motion.directions.join('+')" in MOUNT_JS
    assert 'String(motion.speed)' in MOUNT_JS

def test_transition_stops_old_motion_before_speed_and_new_start():
    pump = _between(MOUNT_JS, 'async function pumpJoystickMotion()', 'function requestMotion(motion)')
    assert pump.index('await stopSlewGesture(previous)') < pump.index('await setSlewSpeedForMotion(target)')
    assert pump.index('await setSlewSpeedForMotion(target)') < pump.index('startSlewGesture(target)')

def test_diagonal_start_uses_two_existing_cardinal_commands():
    assert "label: 'NE', directions: ['north', 'east']" in MOUNT_JS
    assert 'motion.directions.forEach(direction => {' in MOUNT_JS
    assert "direction, gesture_id: gestureId" in MOUNT_JS

def test_no_hold_repetition_timer_is_used_for_slew():
    motion = _between(MOUNT_JS, 'function sendSlewStop(slew)', 'function displayMount(data)')
    assert 'setInterval' not in motion
    assert 'setTimeout' not in motion

def test_same_rig_refresh_does_not_stop_held_joystick():
    handler = _between(MOUNT_JS, "document.addEventListener('controlsrigchange', () => {", "socket.on('connect'")
    assert 'activeSlew && activeSlew.rigId !== selectedRigId' in handler
    prefix = handler.split('if (activeSlew && activeSlew.rigId !== selectedRigId)', 1)[0]
    assert 'releaseJoystick()' not in prefix

def test_homing_disables_joystick_and_preserves_home_cancel():
    assert 'setJoystickEnabled(!homing && speedAvailable);' in MOUNT_JS
    assert 'if (homing || !speedAvailable) releaseJoystick();' in MOUNT_JS
    assert "mountUrl(homing ? 'slew/stop' : 'home')" in MOUNT_JS

def test_tracking_mode_and_switch_still_use_existing_endpoints():
    for endpoint in ("mountUrl('tracking/mode')", "'tracking/start'", "'tracking/stop'"):
        assert endpoint in MOUNT_JS

def test_tracking_mode_change_only_posts_selected_mode():
    handler = _between(MOUNT_JS, "trackingMode.addEventListener('change', () => {", "trackingSwitch.addEventListener")
    assert "mountUrl('tracking/mode')" in handler
    assert 'mode: trackingMode.value' in handler

def test_refresh_and_socket_resync_cannot_issue_slew():
    refresh = _between(MOUNT_JS, 'async function refreshMount()', 'function scheduleMountRefresh(delay)')
    assert 'startSlewGesture' not in refresh
    assert 'stopSlewGesture' not in refresh
    assert "socket.on('connect', refreshMount)" in MOUNT_JS

def test_joystick_css_prevents_touch_selection_and_dragging():
    block = re.search(r'\.mount-joystick\s*\{(?P<body>.*?)\}', CSS, re.DOTALL)
    assert block
    for declaration in (r'touch-action:\s*none', r'user-select:\s*none', r'-webkit-user-select:\s*none', r'-webkit-user-drag:\s*none'):
        assert re.search(declaration, block.group('body'))
