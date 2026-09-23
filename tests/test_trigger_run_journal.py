import json

import pytest

from backend.trigger_run_journal import (
    TriggerRunJournal,
    TriggerRunJournalInvalid,
)


def test_journal_lifecycle_is_atomic_and_low_state(tmp_path):
    journal = TriggerRunJournal(tmp_path / "trigger_state.json", boot_id_fn=lambda: "boot-a")

    started = journal.begin_run(
        rig_id=2,
        mode="real",
        selected={
            "circumstances_file": "circ.json",
            "photo_file": "photo.json",
            "exposure_opt_file": "expo.json",
        },
    )

    assert started["status"] == "active"
    assert started["boot_id"] == "boot-a"
    assert started["runtime_recovery_count"] == 0
    assert started["child_recovery_count"] == 0
    assert journal.active_entries()[0]["run_id"] == started["run_id"]

    claimed = journal.claim_runtime_recovery(
        rig_id=2,
        run_id=started["run_id"],
    )
    assert claimed["runtime_recovery_count"] == 1
    assert journal.claim_runtime_recovery(
        rig_id=2,
        run_id=started["run_id"],
    ) is None

    child = journal.note_child_recovery(
        rig_id=2,
        run_id=started["run_id"],
    )
    assert child["child_recovery_count"] == 1
    assert journal.note_child_recovery(
        rig_id=2,
        run_id=started["run_id"],
    ) is None

    final = journal.finish(
        rig_id=2,
        run_id=started["run_id"],
        status="failed",
        failure_code="CHILD_EXIT",
        detail="child exited unexpectedly",
        exit_code=2,
    )
    assert final["status"] == "failed"
    assert journal.active_entries() == ()

    persisted = json.loads((tmp_path / "trigger_state.json").read_text())
    assert persisted["schema_version"] == 1
    assert persisted["rigs"]["2"]["failure_code"] == "CHILD_EXIT"


def test_journal_wrong_run_id_cannot_mutate_newer_run(tmp_path):
    journal = TriggerRunJournal(tmp_path / "trigger_state.json", boot_id_fn=lambda: "boot-a")
    old = journal.begin_run(rig_id=1, mode="real", selected={})
    new = journal.begin_run(rig_id=1, mode="real", selected={})

    assert journal.finish(
        rig_id=1,
        run_id=old["run_id"],
        status="failed",
    ) is None
    assert journal.active_entries()[0]["run_id"] == new["run_id"]


def test_corrupt_journal_is_reported_explicitly(tmp_path):
    path = tmp_path / "trigger_state.json"
    path.write_text("{not-json", encoding="utf-8")
    journal = TriggerRunJournal(path, boot_id_fn=lambda: "boot-a")

    with pytest.raises(TriggerRunJournalInvalid, match="invalid JSON"):
        journal.active_entries()


def test_journal_write_fsyncs_file_and_parent_directory(tmp_path, monkeypatch):
    fsync_calls = []
    monkeypatch.setattr(
        "backend.trigger_run_journal.os.fsync",
        lambda fd: fsync_calls.append(fd),
    )

    journal = TriggerRunJournal(
        tmp_path / "trigger_state.json",
        boot_id_fn=lambda: "boot-a",
    )
    journal.begin_run(rig_id=1, mode="real", selected={})

    assert len(fsync_calls) == 2
    assert (tmp_path / "trigger_state.json").exists()


def test_invalid_schema_is_reported_explicitly(tmp_path):
    path = tmp_path / "trigger_state.json"
    path.write_text(
        json.dumps({"schema_version": 999, "rigs": {}}),
        encoding="utf-8",
    )
    journal = TriggerRunJournal(path, boot_id_fn=lambda: "boot-a")

    with pytest.raises(TriggerRunJournalInvalid, match="schema_version"):
        journal.snapshot()
