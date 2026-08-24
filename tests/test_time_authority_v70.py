from datetime import datetime, timezone, timedelta
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.trigger_runtime import RuntimeClock
from services.camera_service import CameraService
from plugins.camera.base import seconds_until_deadline, CaptureResult


class FakeTime:
    def __init__(self):
        self.wall = datetime(2027, 8, 2, 9, 0, 0, tzinfo=timezone.utc)
        self.mono = 1000.0
    def wall_now(self): return self.wall
    def monotonic(self): return self.mono
    def sleep(self, seconds): self.mono += seconds


def test_runtime_clock_ignores_wall_clock_jump():
    ft = FakeTime()
    clock = RuntimeClock(wall_clock_fn=ft.wall_now, monotonic_fn=ft.monotonic, sleep_fn=ft.sleep)
    clock.configure(simulate=False)
    assert clock.now() == datetime(2027, 8, 2, 9, 0, 0)
    ft.mono += 10
    assert clock.now() == datetime(2027, 8, 2, 9, 0, 10)
    # Simulate NTP/GPS/iPad-related system correction: wall clock jumps +2 hours.
    ft.wall += timedelta(hours=2)
    ft.mono += 5
    assert clock.now() == datetime(2027, 8, 2, 9, 0, 15)


def test_runtime_clock_simulation_is_monotonic():
    ft = FakeTime()
    clock = RuntimeClock(wall_clock_fn=ft.wall_now, monotonic_fn=ft.monotonic, sleep_fn=ft.sleep)
    clock.configure(simulate=True, speed=60)
    clock.start_simulation(datetime(2027, 8, 2, 8, 0, 0))
    ft.mono += 2
    assert clock.now() == datetime(2027, 8, 2, 8, 2, 0)
    ft.wall -= timedelta(hours=8)
    ft.mono += 1
    assert clock.now() == datetime(2027, 8, 2, 8, 3, 0)


class FakePlugin:
    def __init__(self): self.deadline = None
    def shoot_speeds(self, *args, **kwargs):
        self.deadline = kwargs.get('deadline')
        return CaptureResult(1, 1, 'ok')


def test_camera_service_converts_phase_deadline_to_monotonic(monkeypatch):
    ft = FakeTime()
    clock = RuntimeClock(wall_clock_fn=ft.wall_now, monotonic_fn=ft.monotonic, sleep_fn=ft.sleep)
    clock.configure(False)
    service = CameraService(clock=clock)
    plugin = FakePlugin()
    service.plugin = plugin
    monkeypatch.setattr('services.camera_service.time.monotonic', lambda: 5000.0)
    deadline = clock.now() + timedelta(seconds=12)
    service.shoot_speed_list(['1/500'], deadline=deadline)
    assert isinstance(plugin.deadline, float)
    assert abs(plugin.deadline - 5012.0) < 0.001


def test_numeric_deadline_uses_monotonic(monkeypatch):
    monkeypatch.setattr('plugins.camera.base.time.monotonic', lambda: 100.0)
    assert seconds_until_deadline(107.5) == 7.5


def test_frontend_time_authority_does_not_use_browser_wall_clock_offset():
    html = (ROOT/'flask_app/templates/index.html').read_text(encoding='utf-8')
    update_time = re.search(
        r"function updateTime\(t\)\s*\{(?P<body>.*?)\n\}",
        html,
        re.DOTALL,
    )
    assert update_time is not None
    update_time_body = update_time.group('body')
    assert 'Number.isFinite(t.backend_utc_epoch_ms)' in update_time_body
    assert 'piMs = t.backend_utc_epoch_ms;' in update_time_body
    assert 'Number.isFinite(t.backend_local_epoch_ms)' in update_time_body
    assert 'piLocalMs = t.backend_local_epoch_ms;' in update_time_body
    assert 'Number.isFinite(t.epoch_ms)' in update_time_body
    assert '_clockAnchorEpochMs = piMs;' in update_time_body
    assert '_clockAnchorUtcMs = piMs;' in update_time_body
    assert '_clockAnchorLocalMs = piLocalMs;' in update_time_body
    assert '_clockAnchorPerfMs = performance.now();' in update_time_body
    assert update_time_body.index('_clockAnchorEpochMs = piMs;') < update_time_body.index(
        '_clockAnchorPerfMs = performance.now();'
    )
    assert 'Date.now()' not in update_time_body
    assert 'let _clockAnchorEpochMs = null;' in html
    assert 'let _clockAnchorPerfMs = null;' in html
    assert 'let _clockAnchorEpochMs = Date.now();' not in html
    assert html.index('_clockAnchorEpochMs = piMs;') < html.index('setInterval(_tickClock, 1000)')
    assert 'Date.now() + _clockOffset' not in html
    assert 'offset = -now.getTimezoneOffset() / 60' not in html


def test_frontend_displays_recompute_time_from_anchor_on_every_refresh():
    html = (ROOT/'flask_app/templates/index.html').read_text(encoding='utf-8')
    tick_clock_body = html[
        html.index('function _tickClock() {'):html.index('function _updateGpsBadge(')
    ]
    countdown_body = html[
        html.index('function updateCountdowns(data) {'):html.index('function fmt(')
    ]
    display_time_code = tick_clock_body + countdown_body

    assert 'const now = _nowAdjusted();' in tick_clock_body
    assert 'const nowUtcMs = _nowAdjusted().getTime();' in countdown_body
    assert 'Date.now()' not in display_time_code
    assert not re.search(r'\bdisplayed\s*\+=\s*1000\b', html, re.IGNORECASE)
    assert not re.search(r'\b(?:now|time|timestamp|epoch|clock)\w*\s*\+=\s*1000\b', display_time_code)
    assert 'setInterval(_tickClock, 1000)' in html
    assert re.search(
        r'setInterval\(\s*\(\)\s*=>\s*\{\s*if \(state\.eclipse\) '
        r'updateCountdowns\(state\.eclipse\);\s*\},\s*1000\s*\)',
        html,
    )


def test_header_clock_does_not_use_browser_locale_or_timezone_offset():
    html = (ROOT/'flask_app/templates/index.html').read_text(encoding='utf-8')
    header_clock = html[
        html.index('function _tickClock() {'):html.index('function _updateGpsBadge(')
    ]

    assert not re.search(r'\.toLocaleTimeString\s*\(', header_clock)
    assert not re.search(r'\.getTimezoneOffset\s*\(', header_clock)


def test_frontend_connect_fetches_status_and_reanchors_time():
    html = (ROOT/'flask_app/templates/index.html').read_text(encoding='utf-8')
    assert re.search(
        r"socket\.on\(\s*['\"]connect['\"]\s*,\s*async\s*\(\)\s*=>\s*\{.*?"
        r"fetch\(\s*['\"]/api/status['\"]\s*\).*?"
        r"updateTime\(\s*status\.time\s*\)",
        html,
        re.DOTALL,
    )


def test_gps_sync_is_blocked_while_trigger_runs():
    app = (ROOT/'flask_app/app.py').read_text(encoding='utf-8')
    route = app[app.index('def api_gps_sync():'):app.index('@app.route("/api/gps/state")')]
    assert 'trigger_state.get("running")' in route
    assert 'TRIGGER_RUNNING' in route


def test_trigger_uses_independent_date_and_contact_times():
    src = (ROOT/'scripts/eclipse_trigger.py').read_text(encoding='utf-8')
    assert 'build_timeline' in src
    assert 'rebase_timeline' in src
    assert 'contacts_utc' not in src

def test_frontend_timezone_accepts_numeric_and_string_without_ipad_locale():
    html = (ROOT/'flask_app/templates/index.html').read_text(encoding='utf-8')
    assert "typeof tzValue === 'number'" in html
    assert "typeof tzValue === 'string'" in html
    assert 'toLocaleTimeString()' not in html
