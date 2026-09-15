import io
from pathlib import Path

import backend.trigger_service as trigger_service
from backend.trigger_service import TriggerService


class _State:
    def __init__(self):
        self.updates = []

    def update_trigger_rig(self, rig_id, payload):
        self.updates.append((rig_id, dict(payload)))


class _Proc:
    def __init__(self, lines):
        self.stdout = io.StringIO("".join(lines))
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def _service(tmp_path):
    script = tmp_path / "scripts" / "eclipse_trigger.py"
    script.parent.mkdir(parents=True)
    script.write_text("# test\n", encoding="utf-8")

    state = _State()
    emitted = []
    logs = []
    service = TriggerService(
        state_store=state,
        trigger_script=script,
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


def test_totality_override_ignores_buffered_phase_events(monkeypatch, tmp_path):
    service, state, emitted, logs = _service(tmp_path)
    rig_id = 1

    service._active_photo_paths[rig_id] = tmp_path / "photo.json"
    service._analysis_suppressed_by_rig[rig_id] = True

    proc = _Proc(
        [
            "TRIGGER_PHASE partial_before\n",
            "TRIGGER_PHASE diamond_ring_c2\n",
            "TRIGGER_PHASE totality\n",
        ]
    )
    monkeypatch.setattr(trigger_service.subprocess, "Popen", lambda *a, **k: proc)

    service._run(rig_id=rig_id, totality_only=True)

    phases = [
        payload["phase"]
        for update_rig, payload in state.updates
        if update_rig == rig_id and "phase" in payload
    ]

    # _run itself publishes totality_override, and finally idle. Buffered
    # child phase events must not insert partial/diamond_ring/totality between.
    assert phases == ["totality_override", "idle"]

    emitted_phases = [
        payload["phase"]
        for event, payload in emitted
        if event == "trigger_phase"
    ]
    assert emitted_phases == ["totality_override", "idle"]

    # Suppression blocks state mutation, not observability: lines are still
    # drained and logged.
    logged_text = [item[0] for item in logs]
    assert any("Phase 1" in text for text in logged_text)
    assert any("Phase 2" in text for text in logged_text)
    assert any("Phase 3" in text for text in logged_text)


def test_normal_runtime_phase_events_still_update_state(monkeypatch, tmp_path):
    service, state, emitted, _logs = _service(tmp_path)
    rig_id = 1

    service._active_circumstances_paths[rig_id] = tmp_path / "circ.json"
    service._active_photo_paths[rig_id] = tmp_path / "photo.json"
    service._active_exposure_opt_paths[rig_id] = tmp_path / "exp.json"
    service._analysis_suppressed_by_rig[rig_id] = False

    proc = _Proc(["TRIGGER_PHASE totality\n"])
    monkeypatch.setattr(trigger_service.subprocess, "Popen", lambda *a, **k: proc)

    service._run(rig_id=rig_id)

    phases = [
        payload["phase"]
        for update_rig, payload in state.updates
        if update_rig == rig_id and "phase" in payload
    ]
    assert phases == ["waiting", "totality", "idle"]
