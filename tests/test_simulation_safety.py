from pathlib import Path

SRC = (Path(__file__).parents[1] / 'scripts' / 'eclipse_trigger.py').read_text(encoding='utf-8')


def test_simulation_has_no_camera_service_connection():
    assert '⚡ SIM : accès matériel caméra totalement désactivé' in SRC
    assert 'camera_service = None' in SRC
    assert 'camera_service = CameraService(log_fn=_log, clock=_runtime_clock)' in SRC


def test_capture_path_short_circuits_to_simulation():
    marker = 'def capture_speed_list(camera_service, speeds, photo_num_start, next_shot_time, deadline=None):'
    block = SRC[SRC.index(marker):SRC.index('def attendre_heure', SRC.index(marker))]
    assert 'if _sim_mode:' in block
    assert 'return _sim_capture_speed_list' in block


def test_trigger_engine_contains_no_direct_capture_ptp():
    assert '.trigger_capture()' not in SRC
    assert 'set_config_value(' not in SRC
    assert '"shutterspeed2"' not in SRC


def test_audio_shutdown_is_explicit():
    assert 'def _shutdown_audio_threads' in SRC
    assert '_shutdown_audio_threads()' in SRC


def _load_estimated_photo():
    import ast, math
    tree = ast.parse(SRC)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'estimatedPhoto')
    module = ast.Module(body=[fn], type_ignores=[])
    ns = {'math': math}
    exec(compile(module, '<estimatedPhoto>', 'exec'), ns)
    return ns['estimatedPhoto']


def test_estimated_photo_counts_immediate_first_shot():
    from datetime import datetime, timedelta
    estimated = _load_estimated_photo()
    t0 = datetime(2026, 8, 19, 21, 46, 56)
    assert estimated(t0, t0 + timedelta(seconds=45), 180) == 1
    assert estimated(t0, t0 + timedelta(seconds=40), 4) == 10
    assert estimated(t0, t0, 180) == 0
