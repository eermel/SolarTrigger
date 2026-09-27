import json
import logging

from backend.state_store import StateStore


def test_corrupt_state_file_is_logged_and_defaults_are_used(tmp_path, caplog):
    state_path = tmp_path / "state.json"
    state_path.write_text("{broken-json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="backend.state_store"):
        store = StateStore(state_path)

    assert store.snapshot("gps")["synced"] is False
    assert store.snapshot("devices")["camera"] == {
        "plugin": "none",
        "active": False,
    }
    assert "Unable to load persisted state" in caplog.text
    assert str(state_path) in caplog.text
    assert "JSONDecodeError" in caplog.text


def test_state_save_fsyncs_file_and_parent_directory(tmp_path, monkeypatch):
    from backend import state_store as state_store_module

    state_path = tmp_path / "state.json"
    fsync_calls = []

    monkeypatch.setattr(
        state_store_module.os,
        "fsync",
        lambda fd: fsync_calls.append(fd),
    )

    store = StateStore(state_path)
    store.update_section(
        "gps",
        {
            "synced": True,
            "timezone_name": "Europe/Paris",
        },
        persist=True,
    )

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["gps"]["synced"] is True
    assert saved["gps"]["timezone_name"] == "Europe/Paris"
    assert len(fsync_calls) == 2
    assert not state_path.with_suffix(".json.tmp").exists()
