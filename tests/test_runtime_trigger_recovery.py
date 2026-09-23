import json

import pytest

from backend.trigger_run_journal import TriggerRunJournal


class FakeCameraRuntime:
    def __init__(self, *args, **kwargs):
        pass


class FakeTriggerService:
    recovered = []
    failures = []

    def __init__(self, *args, **kwargs):
        pass

    def recover_persisted_run(self, entry):
        self.__class__.recovered.append(dict(entry))
        return True

    def publish_external_failure(self, rig_id, code, detail, **kwargs):
        self.__class__.failures.append((rig_id, code, detail))


@pytest.fixture(autouse=True)
def _reset_fake_trigger():
    FakeTriggerService.recovered = []
    FakeTriggerService.failures = []


def _prepare_project(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "eclipse_trigger.py").write_text("", encoding="utf-8")
    (tmp_path / "configs").mkdir()
    (tmp_path / "var" / "state").mkdir(parents=True)
    return tmp_path


def test_runtime_recovers_one_active_same_boot_real_run(tmp_path, monkeypatch):
    root = _prepare_project(tmp_path)
    path = root / "var" / "state" / "trigger_state.json"
    journal = TriggerRunJournal(path)
    started = journal.begin_run(
        rig_id=1,
        mode="real",
        selected={
            "circumstances_file": "circ.json",
            "photo_file": "photo.json",
            "exposure_opt_file": "expo.json",
        },
    )

    import backend.runtime_daemon as runtime_daemon

    monkeypatch.setattr(runtime_daemon, "CameraWorkerRuntime", FakeCameraRuntime)
    monkeypatch.setattr(runtime_daemon, "TriggerService", FakeTriggerService)
    runtime_daemon.RuntimeController(root)

    assert len(FakeTriggerService.recovered) == 1
    recovered = FakeTriggerService.recovered[0]
    assert recovered["run_id"] == started["run_id"]
    assert recovered["runtime_recovery_count"] == 1
    assert TriggerRunJournal(path).active_entries()[0]["runtime_recovery_count"] == 1


def test_runtime_fails_closed_across_boot_boundary(tmp_path, monkeypatch):
    root = _prepare_project(tmp_path)
    path = root / "var" / "state" / "trigger_state.json"
    journal = TriggerRunJournal(path)
    started = journal.begin_run(rig_id=2, mode="real", selected={})
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rigs"]["2"]["boot_id"] = "definitely-not-this-boot"
    path.write_text(json.dumps(payload), encoding="utf-8")

    import backend.runtime_daemon as runtime_daemon

    monkeypatch.setattr(runtime_daemon, "CameraWorkerRuntime", FakeCameraRuntime)
    monkeypatch.setattr(runtime_daemon, "TriggerService", FakeTriggerService)
    runtime_daemon.RuntimeController(root)

    assert FakeTriggerService.recovered == []
    assert FakeTriggerService.failures
    assert FakeTriggerService.failures[0][1] == "RUNTIME_REBOOT_DURING_RUN"
    assert TriggerRunJournal(path).active_entries() == ()
    final = TriggerRunJournal(path).snapshot()["rigs"]["2"]
    assert final["run_id"] == started["run_id"]
    assert final["status"] == "failed"


def test_runtime_recovery_is_single_attempt(tmp_path, monkeypatch):
    root = _prepare_project(tmp_path)
    path = root / "var" / "state" / "trigger_state.json"
    journal = TriggerRunJournal(path)
    started = journal.begin_run(rig_id=1, mode="real", selected={})
    journal.claim_runtime_recovery(rig_id=1, run_id=started["run_id"])

    import backend.runtime_daemon as runtime_daemon

    monkeypatch.setattr(runtime_daemon, "CameraWorkerRuntime", FakeCameraRuntime)
    monkeypatch.setattr(runtime_daemon, "TriggerService", FakeTriggerService)
    runtime_daemon.RuntimeController(root)

    assert FakeTriggerService.recovered == []
    assert FakeTriggerService.failures[0][1] == "RECOVERY_LIMIT_REACHED"
    assert TriggerRunJournal(path).active_entries() == ()


def test_runtime_restores_final_failure_after_repeated_restart(tmp_path, monkeypatch):
    root = _prepare_project(tmp_path)
    path = root / "var" / "state" / "trigger_state.json"
    journal = TriggerRunJournal(path)
    started = journal.begin_run(rig_id=3, mode="real", selected={})
    journal.finish(
        rig_id=3,
        run_id=started["run_id"],
        status="failed",
        failure_code="CHILD_EXIT",
        detail="scheduler exited unexpectedly",
        exit_code=2,
    )

    import backend.runtime_daemon as runtime_daemon

    monkeypatch.setattr(runtime_daemon, "CameraWorkerRuntime", FakeCameraRuntime)
    monkeypatch.setattr(runtime_daemon, "TriggerService", FakeTriggerService)

    runtime_daemon.RuntimeController(root)
    runtime_daemon.RuntimeController(root)

    restored = [item for item in FakeTriggerService.failures if item[0] == 3]
    assert len(restored) == 2
    assert all(item[1] == "CHILD_EXIT" for item in restored)
    assert all(item[2] == "scheduler exited unexpectedly" for item in restored)


@pytest.mark.parametrize("status", ["completed", "stopped"])
def test_runtime_does_not_restore_non_failure_final_state(
    tmp_path, monkeypatch, status
):
    root = _prepare_project(tmp_path)
    path = root / "var" / "state" / "trigger_state.json"
    journal = TriggerRunJournal(path)
    started = journal.begin_run(rig_id=1, mode="real", selected={})
    journal.finish(
        rig_id=1,
        run_id=started["run_id"],
        status=status,
    )

    import backend.runtime_daemon as runtime_daemon

    monkeypatch.setattr(runtime_daemon, "CameraWorkerRuntime", FakeCameraRuntime)
    monkeypatch.setattr(runtime_daemon, "TriggerService", FakeTriggerService)

    runtime_daemon.RuntimeController(root)

    assert FakeTriggerService.failures == []
    assert FakeTriggerService.recovered == []


def test_runtime_surfaces_invalid_recovery_journal(tmp_path, monkeypatch):
    root = _prepare_project(tmp_path)
    path = root / "var" / "state" / "trigger_state.json"
    path.write_text("{broken-json", encoding="utf-8")

    import backend.runtime_daemon as runtime_daemon

    monkeypatch.setattr(runtime_daemon, "CameraWorkerRuntime", FakeCameraRuntime)
    monkeypatch.setattr(runtime_daemon, "TriggerService", FakeTriggerService)

    runtime_daemon.RuntimeController(root)

    assert FakeTriggerService.recovered == []
    assert len(FakeTriggerService.failures) == 4
    assert {item[0] for item in FakeTriggerService.failures} == {1, 2, 3, 4}
    assert {
        item[1] for item in FakeTriggerService.failures
    } == {"RECOVERY_JOURNAL_INVALID"}
