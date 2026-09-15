import signal
import threading

from backend.trigger_service import TriggerService


class _Proc:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.sent = []
        self.returncode = None

    def poll(self):
        return None

    def send_signal(self, sig):
        self.sent.append(sig)
        if self.fail:
            raise OSError("signal delivery failed")


class _State:
    def __init__(self):
        self.updates = []

    def update_trigger_rig(self, rig_id, payload):
        self.updates.append((rig_id, dict(payload)))


def _service(tmp_path):
    state = _State()
    emitted = []
    logs = []
    service = TriggerService(
        state_store=state,
        trigger_script=tmp_path / "eclipse_trigger.py",
        json_file=tmp_path / "unused.json",
        configs_dir=tmp_path,
        log_fn=lambda text, level="info", source="trigger", **kwargs: logs.append(
            (text, level, source, kwargs)
        ),
        emit_fn=lambda event, payload: emitted.append((event, dict(payload))),
        camera_runtime=None,
        rig_config_loader=None,
    )
    return service, state, emitted, logs


def test_failed_override_signal_restores_unsuppressed_state(tmp_path):
    service, state, emitted, logs = _service(tmp_path)
    proc = _Proc(fail=True)
    service._procs[1] = proc
    service._analysis_suppressed_by_rig[1] = False

    assert service.override_totality(1) is False

    assert proc.sent == [signal.SIGUSR1]
    assert service._analysis_suppressed_by_rig[1] is False
    assert state.updates == []
    assert emitted == []
    assert any("Totality override error" in item[0] for item in logs)


def test_failed_override_signal_preserves_preexisting_suppression(tmp_path):
    service, _state, _emitted, _logs = _service(tmp_path)
    proc = _Proc(fail=True)
    service._procs[1] = proc
    service._analysis_suppressed_by_rig[1] = True

    assert service.override_totality(1) is False
    assert service._analysis_suppressed_by_rig[1] is True


def test_successful_override_keeps_suppression_and_publishes_override(tmp_path):
    service, state, emitted, _logs = _service(tmp_path)
    proc = _Proc(fail=False)
    service._procs[1] = proc

    assert service.override_totality(1) is True

    assert proc.sent == [signal.SIGUSR1]
    assert service._analysis_suppressed_by_rig[1] is True
    assert state.updates[-1] == (1, {"phase": "totality_override"})
    assert emitted[-1] == (
        "trigger_phase",
        {"rig_id": 1, "phase": "totality_override"},
    )


def test_override_signal_delivery_is_serialized_by_service_lock(tmp_path):
    service, _state, _emitted, _logs = _service(tmp_path)

    entered = threading.Event()
    release = threading.Event()

    class _BlockingProc(_Proc):
        def send_signal(self, sig):
            self.sent.append(sig)
            entered.set()
            assert release.wait(1.0)

    proc = _BlockingProc()
    service._procs[1] = proc

    result = {}

    thread = threading.Thread(
        target=lambda: result.setdefault("value", service.override_totality(1))
    )
    thread.start()
    assert entered.wait(1.0)

    acquired = service._lock.acquire(blocking=False)
    if acquired:
        service._lock.release()

    assert acquired is False

    release.set()
    thread.join(1.0)
    assert not thread.is_alive()
    assert result["value"] is True
