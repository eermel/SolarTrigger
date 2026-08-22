from datetime import datetime, timezone
from types import SimpleNamespace

from plugins.camera.base import CaptureResult
from services.camera_service import (
    CameraService,
    CaptureIntent,
    PreparedCapture,
    _normalized_speed_plan,
)


class FakeCamera:
    def __init__(self): self.init_count = 0; self.exit_count = 0
    def init(self): self.init_count += 1
    def exit(self): self.exit_count += 1


class FakePlugin:
    name = 'fake'
    def __init__(self, camera): self.camera=camera; self.calls=[]
    def init_settings(self, **kwargs): self.calls.append(('init', kwargs))
    def set_exposure_settings(self, **kwargs): self.calls.append(('exposure', kwargs))
    def get_battery_level(self): return 73
    def shoot_speeds(self, vmax, vmin, step, photo_num_start=0, deadline=None):
        self.calls.append(('range', vmax, vmin, step))
        return CaptureResult(frames=3, planned=3, detail='range')
    def shoot_single(self, speed, photo_num=0, deadline=None):
        self.calls.append(('single', speed))
        return CaptureResult(frames=1, planned=1, detail='single')


class FakePreparedPlugin(FakePlugin):
    def prepare_capture(self, intent):
        self.calls.append(('prepare', intent))
        planned_count = len(intent.speeds) if intent.speeds else 3
        return PreparedCapture(
            token=intent,
            estimated_total_s=0.5,
            exposures_s=None,
            planned_count=planned_count,
            plugin_name=self.name,
        )

    def trigger_prepared(self, prepared, deadline=None):
        self.calls.append(('trigger', prepared, deadline))
        intent = prepared.token
        if intent.speeds:
            frames = 0
            for speed in intent.speeds:
                frames += self.shoot_single(
                    speed,
                    photo_num=frames,
                    deadline=deadline,
                ).frames
            return CaptureResult(
                frames=frames,
                planned=len(intent.speeds),
                detail='explicit speed list',
            )
        return CaptureResult(frames=3, planned=3, detail='prepared range')


class FakeClock:
    def __init__(self, remaining_seconds):
        self.remaining_seconds = remaining_seconds
        self.deadlines = []

    def remaining(self, deadline):
        self.deadlines.append(deadline)
        return self.remaining_seconds


def loader(camera, log): return FakePlugin(camera)


class FakeSyncPlugin:
    name = 'fake-sync'

    def __init__(self, camera, result):
        self.camera = camera
        self.result = result
        self.refs = []

    def sync_datetime(self, ref):
        self.refs.append(ref)
        return self.result


def make_sync_service(monkeypatch, result):
    camera = FakeCamera()
    plugin = FakeSyncPlugin(camera, result)
    monkeypatch.setattr(
        'services.camera_service.get_camera_model',
        lambda connected_camera: 'Test Camera Model',
    )
    service = CameraService(
        camera_factory=lambda: camera,
        plugin_loader=lambda connected_camera, log: plugin,
        log_fn=lambda *args: None,
    )
    return service, camera, plugin


def test_regular_speed_list_becomes_brand_neutral_range(monkeypatch):
    monkeypatch.setattr('services.camera_service.get_camera_model', lambda camera: 'Sony ILCE-7M5 (PC Control)')
    svc=CameraService(camera_factory=FakeCamera, plugin_loader=loader, log_fn=lambda *a:None)
    svc.connect()
    res=svc.shoot_speed_list(['1/500','1/1000','1/2000'])
    assert res.frames == 3
    call=svc.plugin.calls[-1]
    assert call[0]=='range'
    assert call[1]=='1/2000' and call[2]=='1/500'
    assert abs(call[3]-1.0) < 0.05


def test_irregular_speed_list_preserves_exact_values(monkeypatch):
    monkeypatch.setattr('services.camera_service.get_camera_model', lambda camera: 'Nikon DSC D850')
    svc=CameraService(camera_factory=FakeCamera, plugin_loader=loader, log_fn=lambda *a:None)
    svc.connect()
    res=svc.shoot_speed_list(['1/1000','1/500','1/60'])
    assert res.frames == 3
    singles=[c[1] for c in svc.plugin.calls if c[0]=='single']
    assert singles == ['1/1000','1/500','1/60']


def test_service_owns_phase_settings_and_battery(monkeypatch):
    monkeypatch.setattr('services.camera_service.get_camera_model', lambda camera: 'Sony ILCE-7M5')
    svc=CameraService(camera_factory=FakeCamera, plugin_loader=loader, log_fn=lambda *a:None)
    svc.connect(); svc.init_settings(aperture='f/8', iso='100')
    svc.set_exposure_settings(aperture='f/11', iso='200')
    assert svc.get_battery_level() == 73
    assert any(c[0]=='init' for c in svc.plugin.calls)
    assert any(c[0]=='exposure' for c in svc.plugin.calls)


def test_prepare_then_trigger_converts_deadline_at_service_boundary(monkeypatch):
    deadline = datetime(2026, 8, 12, 17, 47, tzinfo=timezone.utc)
    target_time = datetime(2026, 8, 12, 17, 46, tzinfo=timezone.utc)
    clock = FakeClock(4.25)
    plugin = FakePreparedPlugin(FakeCamera())
    service = CameraService(clock=clock)
    service.plugin = plugin
    monkeypatch.setattr('services.camera_service.time.monotonic', lambda: 100.0)
    intent = CaptureIntent(
        shutter_min=None,
        shutter_max=None,
        step_ev=None,
        speeds=['1/500', '1/1000', '1/2000'],
        phase='C2',
        target_time=target_time,
        deadline=deadline,
        overflow_policy='truncate',
    )

    prepared = service.prepare_capture(intent)
    result = service.trigger_prepared(prepared, deadline)

    normalized = plugin.calls[0][1]
    assert normalized.shutter_min == '1/500'
    assert normalized.shutter_max == '1/2000'
    assert normalized.step_ev == 1.0
    assert normalized.speeds is None
    assert normalized.phase == 'C2'
    assert normalized.target_time is target_time
    assert normalized.deadline is deadline
    assert clock.deadlines == [deadline]
    plugin_deadline = plugin.calls[1][2]
    assert isinstance(plugin_deadline, (int, float))
    assert plugin_deadline == 104.25
    assert result.frames == 3
    assert result.planned == 3


def test_prepare_then_trigger_preserves_irregular_speeds_as_singles():
    plugin = FakePreparedPlugin(FakeCamera())
    service = CameraService()
    service.plugin = plugin
    intent = CaptureIntent(
        shutter_min=None,
        shutter_max=None,
        step_ev=None,
        speeds=['1/1000', '1/500', '1/60'],
        phase='C3',
        target_time=datetime(2026, 8, 12, 17, 48),
        deadline=None,
        overflow_policy='truncate',
    )

    prepared = service.prepare_capture(intent)
    result = service.trigger_prepared(prepared, None)

    assert prepared.token.speeds == ['1/1000', '1/500', '1/60']
    singles = [call[1] for call in plugin.calls if call[0] == 'single']
    assert singles == ['1/1000', '1/500', '1/60']
    assert plugin.calls[1][2] is None
    assert result.frames == 3
    assert result.planned == 3


def test_sync_datetime_unsupported_autoconnects_and_fills_defaults(monkeypatch):
    svc, camera, plugin = make_sync_service(
        monkeypatch,
        {'status': 'unsupported', 'message': 'not supported'},
    )
    ref = SimpleNamespace(timezone_name='Europe/Paris', utc_offset_minutes=120)

    result = svc.sync_datetime(ref)

    assert camera.init_count == 1
    assert plugin.refs == [ref]
    assert result == {
        'status': 'unsupported',
        'datetime_synced': False,
        'timezone_synced': False,
        'datetime_applied': None,
        'timezone_name': 'Europe/Paris',
        'utc_offset_minutes': 120,
        'message': 'not supported',
        'plugin': 'fake-sync',
        'model': 'Test Camera Model',
    }


def test_sync_datetime_partial_preserves_fields_and_normalizes_flags(monkeypatch):
    svc, _, _ = make_sync_service(
        monkeypatch,
        {
            'status': 'partial',
            'datetime_synced': True,
            'timezone_synced': 1,
            'datetime_applied': '2026-08-22T10:00:00+00:00',
            'timezone_name': 'plugin-zone',
            'utc_offset_minutes': 30,
            'message': 'clock only',
            'plugin': 'reported-plugin',
            'model': 'reported-model',
        },
    )

    result = svc.sync_datetime(SimpleNamespace())

    assert result['datetime_synced'] is True
    assert result['timezone_synced'] is False
    assert result['datetime_applied'] == '2026-08-22T10:00:00+00:00'
    assert result['timezone_name'] == 'plugin-zone'
    assert result['utc_offset_minutes'] == 30
    assert result['message'] == 'clock only'
    assert result['plugin'] == 'reported-plugin'
    assert result['model'] == 'reported-model'


def test_sync_datetime_ok_passes_through_confirmed_sync(monkeypatch):
    applied = '2026-08-22T12:34:56+02:00'
    svc, _, _ = make_sync_service(
        monkeypatch,
        {
            'status': 'ok',
            'datetime_synced': True,
            'timezone_synced': True,
            'datetime_applied': applied,
        },
    )

    result = svc.sync_datetime(None)

    assert result['status'] == 'ok'
    assert result['datetime_synced'] is True
    assert result['timezone_synced'] is True
    assert result['datetime_applied'] == applied
    assert result['plugin'] == 'fake-sync'
    assert result['model'] == 'Test Camera Model'


def test_sync_datetime_error_preserves_message_without_claiming_sync(monkeypatch):
    svc, _, _ = make_sync_service(
        monkeypatch,
        {
            'status': 'error',
            'datetime_synced': False,
            'message': 'camera rejected update',
        },
    )

    result = svc.sync_datetime(None)

    assert result['status'] == 'error'
    assert result['message'] == 'camera rejected update'
    assert result['datetime_synced'] is False
    assert result['timezone_synced'] is False
    assert result['plugin'] == 'fake-sync'
    assert result['model'] == 'Test Camera Model'


def test_speed_plan_single():
    assert _normalized_speed_plan(['1/500']) == ('1/500','1/500',1.0,True)


def test_generic_ptp_ability_does_not_mask_specific_config_model():
    from plugins.camera import get_camera_model
    class Ability: model='USB PTP Class Camera'
    class Widget:
        def get_value(self): return 'Sony ILCE-7M5 (PC Control)'
    class Config:
        def get_child_by_name(self, name):
            if name == 'cameramodel': return Widget()
            raise RuntimeError(name)
    class Camera:
        def get_abilities(self): return Ability()
        def get_config(self): return Config()
    assert get_camera_model(Camera()).startswith('Sony ILCE-7M5')
