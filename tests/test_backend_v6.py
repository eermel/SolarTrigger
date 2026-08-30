import json
import os
import sys
import threading
import types
from datetime import datetime
from pathlib import Path
import pytest

from backend.state_store import StateStore
from backend.trigger_service import validate_eclipse, TriggerValidationError
from backend.trigger_runtime import RuntimeClock, TriggerWatchdog


def test_state_store_persists_only_runtime_configuration(tmp_path):
    path=tmp_path/'state.json'; store=StateStore(path)
    store.update_section('gps', {'synced': True, 'lat': 1.2, 'hdop': 0.7})
    store.set('gps_sync_running', True)
    store.save()
    restored=StateStore(path)
    assert restored.snapshot('gps')['lat'] == 1.2
    assert restored.snapshot('gps')['hdop'] == 0.7
    assert restored.get('gps_sync_running') is False


def test_state_store_gps_timezone_defaults_survive_save_and_reload(tmp_path):
    path = tmp_path / 'state.json'
    store = StateStore(path)

    gps = store.snapshot('gps')
    assert gps['timezone_name'] is None
    assert gps['utc_offset_minutes'] is None

    store.save()
    restored_gps = StateStore(path).snapshot('gps')
    assert restored_gps['timezone_name'] is None
    assert restored_gps['utc_offset_minutes'] is None


def test_state_store_persists_focuser_settings(tmp_path):
    path = tmp_path / 'state.json'
    store = StateStore(path)

    assert store.snapshot('focuser_settings') == {
        'mode': 'slow',
        'slow_step': 20,
        'fast_step': 150,
        'updated_at': None,
    }

    focuser_settings = {
        'mode': 'fast',
        'slow_step': 25,
        'fast_step': 175,
        'updated_at': '2026-08-22T12:34:56Z',
    }
    store.update_section('focuser_settings', focuser_settings)
    store.save()

    assert StateStore(path).snapshot('focuser_settings') == focuser_settings
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['focuser_settings'] == focuser_settings


def test_state_store_persists_only_configured_device_fields(tmp_path):
    path = tmp_path / 'state.json'
    store = StateStore(path)
    store.update_section('devices', {
        'camera': {
            'plugin': 'gphoto2',
            'active': True,
            'detected_model': 'Test Camera',
            'suggested_plugin': 'other-camera-plugin',
            'scan_status': 'complete',
        },
        'gps': {'plugin': 'gpsd', 'active': True, 'detected_port': '/dev/test'},
        'updated_at': '2026-08-22T12:34:56Z',
        'scan_status': 'complete',
    })

    store.save()
    restored_devices = StateStore(path).snapshot('devices')

    assert restored_devices == {
        'camera': {'plugin': 'gphoto2', 'active': True},
        'gps': {'plugin': 'gpsd', 'active': True},
        'focuser': {'plugin': 'none', 'active': False},
        'mount': {'plugin': 'none', 'active': False},
        'updated_at': '2026-08-22T12:34:56Z',
    }


def test_camera_status_preserves_existing_camera_subkeys(monkeypatch):
    class FakeApp:
        def __init__(self, *args, **kwargs):
            self.config = {}

        def route(self, *args, **kwargs):
            return lambda function: function

    class FakeSocketIO:
        def __init__(self, *args, **kwargs): pass
        def emit(self, *args, **kwargs): pass
        def on(self, *args, **kwargs): return lambda function: function

    fake_gp = types.SimpleNamespace(
        Camera=lambda: (_ for _ in ()).throw(RuntimeError())
    )
    fake_flask = types.SimpleNamespace(
        Flask=FakeApp,
        jsonify=lambda value: value,
        request=types.SimpleNamespace(),
        send_from_directory=lambda *args, **kwargs: None,
    )
    fake_socketio = types.SimpleNamespace(
        SocketIO=FakeSocketIO,
        emit=lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(sys.modules, 'gphoto2', fake_gp)
    monkeypatch.setitem(sys.modules, 'flask', fake_flask)
    monkeypatch.setitem(sys.modules, 'flask_socketio', fake_socketio)
    import flask_app.app as flask_module

    camera_state = {'camera': {'time_sync': '2026-08-22T12:34:56Z'}}
    monkeypatch.setattr(flask_module, '_state', camera_state)
    monkeypatch.setattr(flask_module, '_state_lock', threading.RLock())
    monkeypatch.setattr(flask_module, '_save_state', lambda: None)

    status = flask_module._get_camera_status()

    assert status['time_sync'] == '2026-08-22T12:34:56Z'
    assert camera_state['camera']['time_sync'] == '2026-08-22T12:34:56Z'
    assert status == {'time_sync': '2026-08-22T12:34:56Z'}


def test_state_store_has_circumstances_and_capture_defaults(tmp_path):
    store = StateStore(tmp_path / 'state.json')

    expected = {'loaded': False, 'active_file': None, 'meta': {}}
    assert store.snapshot('circumstances') == expected
    assert store.snapshot('capture') == expected


def test_state_store_restores_circumstances_and_capture_files(tmp_path):
    path = tmp_path / 'state.json'
    store = StateStore(path)
    store.update_section(
        'circumstances',
        {'loaded': True, 'active_file': 'circumstances.json', 'meta': {'site': 'test'}},
    )
    store.update_section(
        'capture',
        {'loaded': True, 'active_file': 'capture.json', 'meta': {'camera': 'test'}},
    )
    store.save()

    restored = StateStore(path)
    assert restored.snapshot('circumstances') == {
        'loaded': False,
        'active_file': 'circumstances.json',
        'meta': {'site': 'test'},
    }
    assert restored.snapshot('capture') == {
        'loaded': False,
        'active_file': 'capture.json',
        'meta': {'camera': 'test'},
    }


def test_boot_reset_invalidates_gps_and_eclipse(tmp_path):
    store=StateStore(tmp_path/'state.json')
    store.update_section('gps', {'synced': True, 'lat': 42.0})
    store.set('eclipse', {'C1':'10:00:00'})
    store.reset_boot_sensitive()
    assert store.snapshot('gps')['synced'] is False
    assert store.snapshot('gps')['lat'] is None
    assert store.get('eclipse') is None


def test_eclipse_validation_accepts_midnight_rollover():
    validate_eclipse({'TSTART':'23:00:00','C1':'23:30:00','C2':'23:59:00','C3':'00:01:00','C4':'00:30:00','TEND':'01:00:00'})


def test_eclipse_validation_rejects_bad_order():
    with pytest.raises(TriggerValidationError):
        validate_eclipse({'TSTART':'10:00:00','C1':None,'C2':'11:30:00','C3':'12:00:00','C4':None,'TEND':'14:00:00'})


def test_watchdog_roundtrip(tmp_path):
    clock=RuntimeClock(); wd=TriggerWatchdog(tmp_path/'trigger_state.json', clock)
    wd.write('shooting', datetime(2026,8,19,20,0,0))
    assert wd.read()['phase']=='shooting'
    wd.clear(); assert wd.read() is None


def test_trigger_service_simulation_builds_safe_command(tmp_path, monkeypatch):
    from backend.trigger_service import TriggerService
    store=StateStore(tmp_path/'state.json')
    store.update_section('gps', {'synced': True, 'sync_time': datetime.now().isoformat()})
    eclipse=tmp_path/'todayeclipse.json'
    eclipse.write_text(json.dumps({
        'TSTART':'10:00:00','C1':'10:10:00','C2':'10:20:00','C3':'10:21:00','C4':'10:30:00','TEND':'10:40:00'
    }))
    scripts=tmp_path/'scripts'; scripts.mkdir()
    script=scripts/'eclipse_trigger.py'; script.write_text('')
    events=tmp_path/'events.log'; configs=tmp_path/'configs'; configs.mkdir()
    seen={}
    class Stdout:
        def readline(self): return ''
    class Proc:
        returncode=0
        stdout=Stdout()
        def poll(self): return 0
        def wait(self, timeout=None): return 0
    def fake_popen(cmd, **kwargs):
        seen['cmd']=cmd
        seen['kwargs']=kwargs
        return Proc()
    monkeypatch.setattr('backend.trigger_service.subprocess.Popen', fake_popen)
    svc=TriggerService(store,script,eclipse,events,configs,lambda *a:None,lambda *a:None)
    svc._run(simulate=True,speed=120)
    assert '--simulate' in seen['cmd']
    assert seen['cmd'][seen['cmd'].index('--speed')+1] == '120'
    assert Path(seen['kwargs']['cwd']) == tmp_path
    pythonpath = seen['kwargs']['env']['PYTHONPATH'].split(os.pathsep)
    assert pythonpath[0] == str(tmp_path)


def test_trigger_service_rejects_bad_simulation_speed(tmp_path):
    from backend.trigger_service import TriggerService
    store=StateStore(tmp_path/'state.json')
    svc=TriggerService(store,tmp_path/'x.py',tmp_path/'x.json',tmp_path/'events',tmp_path,lambda *a:None,lambda *a:None)
    with pytest.raises(TriggerValidationError) as exc:
        svc.start(simulate=True, speed=0)
    assert exc.value.code == 'SIM_SPEED_INVALID'


def test_trigger_service_simulation_does_not_require_gps(tmp_path, monkeypatch):
    from backend.trigger_service import TriggerService
    store=StateStore(tmp_path/'state.json')
    store.update_section('gps', {'synced': False, 'sync_time': None})
    store.update_section('circumstances', {'loaded': True, 'active_file': 'todayeclipse.json'})
    store.update_section('capture', {'loaded': True, 'active_file': 'camera.json'})
    store.set('camera_config_file', 'camera.json')
    eclipse=tmp_path/'todayeclipse.json'
    eclipse.write_text(json.dumps({
        '_date':datetime.now().astimezone().date().isoformat(),
        'TSTART':'10:00:00','C1':'10:10:00','C2':'10:20:00','C3':'10:21:00','C4':'10:30:00','TEND':'10:40:00'
    }))
    configs=tmp_path/'configs'
    camera_cfg=configs/'camera_cfg'
    camera_cfg.mkdir(parents=True)
    (camera_cfg/'camera.json').write_text('{}')
    script=tmp_path/'eclipse_trigger.py'; script.write_text('')
    svc=TriggerService(store,script,eclipse,tmp_path/'events',configs,lambda *a:None,lambda *a:None)
    # Evite de lancer un vrai thread : le but est de valider les préconditions.
    class DummyThread:
        def __init__(self, *a, **k): pass
        def start(self): pass
    monkeypatch.setattr('backend.trigger_service.threading.Thread', DummyThread)
    assert svc.start(simulate=True, speed=60) is True
    assert store.snapshot('trigger')['mode'] == 'simulation'


def test_trigger_service_real_start_still_requires_gps(tmp_path):
    from backend.trigger_service import TriggerService
    store=StateStore(tmp_path/'state.json')
    store.update_section('gps', {'synced': False, 'sync_time': None})
    eclipse=tmp_path/'todayeclipse.json'
    eclipse.write_text(json.dumps({
        'TSTART':'10:00:00','C1':'10:10:00','C2':'10:20:00','C3':'10:21:00','C4':'10:30:00','TEND':'10:40:00'
    }))
    svc=TriggerService(store,tmp_path/'eclipse_trigger.py',eclipse,tmp_path/'events',tmp_path,lambda *a:None,lambda *a:None)
    with pytest.raises(TriggerValidationError) as exc:
        svc.start(simulate=False)
    assert exc.value.code == 'GPS_NOT_SYNCED'


def test_trigger_service_dryrun_builds_real_camera_command(tmp_path, monkeypatch):
    from backend.trigger_service import TriggerService
    store=StateStore(tmp_path/'state.json')
    store.update_section('gps', {'synced': True, 'sync_time': datetime.now().isoformat()})
    eclipse=tmp_path/'todayeclipse.json'
    eclipse.write_text(json.dumps({'_date':'2027-08-02','TSTART':'10:00:00.125','C1':'10:10:00.250','C2':'10:20:00.375','C3':'10:21:00.500','C4':'10:30:00.625','TEND':'10:40:00.750'}))
    scripts=tmp_path/'scripts'; scripts.mkdir()
    script=scripts/'eclipse_trigger.py'; script.write_text('')
    configs=tmp_path/'configs'; configs.mkdir()
    camera_cfg=configs/'camera_cfg'; camera_cfg.mkdir()
    camera_file=camera_cfg/'camera.json'; camera_file.write_text('{}')
    store.set('camera_config_file', 'camera.json')
    seen={}
    class Stdout:
        def readline(self): return ''
    class Proc:
        returncode=0; stdout=Stdout()
        def poll(self): return 0
        def wait(self, timeout=None): return 0
    def fake_popen(cmd, **kwargs):
        seen['cmd']=cmd
        seen['kwargs']=kwargs
        return Proc()
    monkeypatch.setattr('backend.trigger_service.subprocess.Popen', fake_popen)
    svc=TriggerService(store,script,eclipse,tmp_path/'events',configs,lambda *a:None,lambda *a:None)
    svc._run(dry_run=True,dry_run_delay=45)
    assert '--dry-run' in seen['cmd']
    assert '--dry-run-delay' in seen['cmd']
    assert '--simulate' not in seen['cmd']
    assert seen['cmd'][seen['cmd'].index('--camera') + 1] == str(camera_file)
    assert Path(seen['kwargs']['cwd']) == tmp_path
    pythonpath = seen['kwargs']['env']['PYTHONPATH'].split(os.pathsep)
    assert pythonpath[0] == str(tmp_path)


def test_trigger_service_totality_uses_project_runtime_environment(
    tmp_path, monkeypatch
):
    from backend.trigger_service import TriggerService

    store = StateStore(tmp_path / "state.json")

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    trigger_script = scripts / "eclipse_trigger.py"
    trigger_script.write_text("", encoding="utf-8")
    totality_script = scripts / "totality_only.py"
    totality_script.write_text("", encoding="utf-8")

    configs = tmp_path / "configs"
    camera_cfg = configs / "camera_cfg"
    camera_cfg.mkdir(parents=True)

    camera_file = camera_cfg / "camera.json"
    camera_file.write_text("{}", encoding="utf-8")
    store.set("camera_config_file", "camera.json")

    seen = {}

    class Stdout:
        def readline(self):
            return ""

    class Proc:
        returncode = 0
        stdout = Stdout()

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return Proc()

    monkeypatch.setattr(
        "backend.trigger_service.subprocess.Popen",
        fake_popen,
    )

    svc = TriggerService(
        store,
        trigger_script,
        tmp_path / "todayeclipse.json",
        tmp_path / "events.log",
        configs,
        lambda *args: None,
        lambda *args: None,
    )

    svc._run_totality(totality_script)

    assert seen["cmd"][0:3] == [
        sys.executable,
        "-u",
        str(totality_script),
    ]
    assert seen["cmd"][seen["cmd"].index("--camera") + 1] == str(camera_file)

    assert Path(seen["kwargs"]["cwd"]) == tmp_path

    pythonpath = seen["kwargs"]["env"]["PYTHONPATH"].split(os.pathsep)
    assert pythonpath[0] == str(tmp_path)


def test_totality_only_preempts_running_trigger(tmp_path, monkeypatch):
    from backend.trigger_service import TriggerService

    store = StateStore(tmp_path / "state.json")
    store.set(
        "trigger",
        {"running": True, "phase": "partial", "mode": "real", "speed": 1.0},
    )

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    trigger_script = scripts / "eclipse_trigger.py"
    trigger_script.write_text("", encoding="utf-8")
    totality_script = scripts / "totality_only.py"
    totality_script.write_text("", encoding="utf-8")

    configs = tmp_path / "configs"
    configs.mkdir()

    svc = TriggerService(
        store,
        trigger_script,
        tmp_path / "todayeclipse.json",
        tmp_path / "events.log",
        configs,
        lambda *args: None,
        lambda *args: None,
    )

    class OldProc:
        def __init__(self):
            self.returncode = None
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    old_proc = OldProc()
    svc._proc = old_proc

    launched = []

    def fake_run_totality(script_path):
        launched.append(script_path)

    monkeypatch.setattr(svc, "_run_totality", fake_run_totality)

    class ImmediateThread:
        def __init__(self, *, target, args, name, daemon):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(
        "backend.trigger_service.threading.Thread",
        ImmediateThread,
    )

    assert svc.start_totality_only(totality_script) is True
    assert old_proc.terminated is True
    assert old_proc.killed is False
    assert launched == [totality_script]



def test_eclipse_validation_accepts_partial_without_c2_c3():
    validate_eclipse({
        "TSTART": "16:00:00",
        "C1": "17:00:00",
        "C2": None,
        "TMAX": "18:00:00",
        "C3": None,
        "C4": "19:00:00",
        "TEND": "20:00:00",
    })
