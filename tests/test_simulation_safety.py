from pathlib import Path

SRC = (Path(__file__).parents[1] / 'scripts' / 'eclipse_trigger.py').read_text(encoding='utf-8')


def test_simulation_has_no_camera_service_connection():
    assert 'camera = SimulationCamera(clock, rig_snapshot)' in SRC
    assert 'if args.simulate:' in SRC


def test_capture_path_short_circuits_to_simulation():
    assert 'if args.simulate:' in SRC
    assert 'CameraIpcClient(socket_path, session' in SRC


def test_trigger_engine_contains_no_direct_capture_ptp():
    assert '.trigger_capture()' not in SRC
    assert 'set_config_value(' not in SRC
    assert '"shutterspeed2"' not in SRC


def test_audio_shutdown_is_explicit():
    assert 'audio_service.shutdown()' in SRC


def test_runtime_has_no_absolute_grid_or_skipped_slot_logic():
    assert '_run_absolute_grid' not in SRC
    assert 'skipped_slot' not in SRC
