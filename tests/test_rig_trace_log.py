import builtins
import json
from concurrent.futures import ThreadPoolExecutor

from backend.rig_trace_log import RigTraceLog


def test_parallel_appends_write_valid_json_lines(tmp_path):
    path = tmp_path / "nested" / "rig_traces.jsonl"
    log = RigTraceLog(path)
    entry_count = 100

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda value: log.append({"rig_id": value}), range(entry_count)))

    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == entry_count
    assert {entry["rig_id"] for entry in entries} == set(range(entry_count))


def test_append_ignores_open_error(monkeypatch, tmp_path):
    log = RigTraceLog(tmp_path / "rig_traces.jsonl")

    def fail_open(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(builtins, "open", fail_open)

    log.append({"rig_id": 1})
