import json
from datetime import datetime

import pytest

from backend import rig_trace


def test_trace_event_appends_json_lines(monkeypatch, tmp_path):
    trace_path = tmp_path / "rig_traces.jsonl"
    monkeypatch.setattr(rig_trace, "_PATH", trace_path)

    first = rig_trace.trace_event("test", {"rig_id": 1})
    second = rig_trace.trace_event("ready", {"rig_id": 2})

    lines = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert lines == [first, second]
    assert [(line["kind"], line["rig_id"]) for line in lines] == [
        ("test", 1),
        ("ready", 2),
    ]
    assert all(datetime.fromisoformat(line["timestamp"]).tzinfo is not None for line in lines)


def test_trace_event_requires_dict_payload():
    with pytest.raises(TypeError, match="payload must be a dict"):
        rig_trace.trace_event("test", [])
