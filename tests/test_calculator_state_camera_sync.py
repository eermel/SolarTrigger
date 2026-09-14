from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")


def _function_slice(name, next_marker):
    start = APP.index(name)
    end = APP.index(next_marker, start)
    return APP[start:end]


def test_legacy_camera_sync_hides_internal_exception_text():
    section = _function_slice(
        'def api_camera_sync_time():',
        '@app.route("/api/eclipse/supported")',
    )
    assert '"error": "camera unavailable"' in section
    assert '"code": "CAMERA_UNAVAILABLE"' in section
    assert '"rig_id": 1' in section
    assert 'No camera connected: {exc}' not in section


def test_eclipse_calculator_always_clears_running_state_and_process():
    section = _function_slice(
        'def api_eclipse_calculate():',
        'def _erase_all_persistent_data():',
    )
    assert 'finally:' in section
    assert '_state["calc_running"] = False' in section
    assert '_calc_proc = None' in section


def test_eclipse_calculator_has_unexpected_exception_boundary():
    section = _function_slice(
        'def api_eclipse_calculate():',
        'def _erase_all_persistent_data():',
    )
    assert 'except Exception:' in section
    assert 'app.logger.exception("Python eclipse calculation failed")' in section
    assert '"eclipse_calculated"' in section
    assert '"status": "error"' in section
