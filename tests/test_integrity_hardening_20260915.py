import json
from concurrent.futures import Future
from pathlib import Path
import threading

import pytest

from backend.event_log import EventLog
from backend.state_store import StateStore
from scripts.fanout_camera_adapter import FanoutCameraAdapter


class _FailingIpc:
    def list_active_camera_rigs(self):
        return {"rig_ids": [1]}

    def initialize(self, rig_id, **kwargs):
        raise RuntimeError("camera offline")

    def prepare_capture(self, rig_id, intent):
        raise RuntimeError("camera offline")


class _EmptyIpc:
    def list_active_camera_rigs(self):
        return {"rig_ids": []}


def test_fanout_complete_initialize_failure_is_not_swallowed():
    adapter = FanoutCameraAdapter(_FailingIpc(), log_fn=lambda _msg: None)
    try:
        with pytest.raises(RuntimeError, match="failed on every active camera RIG"):
            adapter.initialize(aperture="f/8", iso="100")
    finally:
        adapter.close()


def test_fanout_complete_prepare_failure_is_not_empty_success():
    adapter = FanoutCameraAdapter(_FailingIpc(), log_fn=lambda _msg: None)
    try:
        with pytest.raises(RuntimeError, match="failed on every active camera RIG"):
            adapter.prepare_capture(object())
    finally:
        adapter.close()


def test_fanout_rejects_session_without_camera_rig():
    adapter = FanoutCameraAdapter(_EmptyIpc(), log_fn=lambda _msg: None)
    try:
        with pytest.raises(RuntimeError, match="no active camera RIG"):
            adapter.initialize()
    finally:
        adapter.close()


def test_state_store_save_keeps_valid_json_under_concurrency(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    errors = []

    def writer(value):
        try:
            for _ in range(30):
                store.update_section("camera", {"battery": value})
                store.save()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(value,)) for value in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert "camera" in parsed
    assert parsed["camera"]["battery"] in range(4)


def test_event_log_reset_keeps_buffer_and_file_consistent(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append("before")
    log.reset()
    assert log.snapshot() == []
    assert log.path.read_text(encoding="utf-8") == ""
