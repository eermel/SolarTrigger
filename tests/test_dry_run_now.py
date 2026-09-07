from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path

import pytest

import backend.trigger_service as trigger_service_module
from backend.execution_plan_runtime import (
    load_execution_plan,
    rebase_execution_plan,
)
from backend.trigger_service import (
    TriggerService,
    TriggerValidationError,
)


ROOT = Path(__file__).resolve().parents[1]

HTML = (
    ROOT / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")

CSS = (
    ROOT / "flask_app" / "static" / "css" / "solartrigger.css"
).read_text(encoding="utf-8")

JS = (
    ROOT / "flask_app" / "static" / "js" / "solartrigger.js"
).read_text(encoding="utf-8")

APP = (
    ROOT / "flask_app" / "app.py"
).read_text(encoding="utf-8")

TRIGGER_SCRIPT = (
    ROOT / "scripts" / "eclipse_trigger.py"
).read_text(encoding="utf-8")


class DummyState:
    def __init__(self):
        self.trigger_updates = []

    def update_trigger_rig(self, rig_id, patch):
        self.trigger_updates.append((rig_id, dict(patch)))

    def snapshot(self, _name):
        return {}

    def get(self, _name, default=None):
        return default


def make_service(tmp_path):
    script = tmp_path / "scripts" / "eclipse_trigger.py"
    script.parent.mkdir(parents=True)
    script.write_text("# test trigger\n", encoding="utf-8")

    configs = tmp_path / "configs"
    configs.mkdir()

    state = DummyState()
    logs = []
    events = []

    service = TriggerService(
        state,
        script,
        tmp_path / "circumstances.json",
        configs,
        log_fn=lambda message, level, source: logs.append(
            (message, level, source)
        ),
        emit_fn=lambda event, payload: events.append(
            (event, payload)
        ),
    )

    return service, state, logs, events


def test_trigger_buttons_are_one_point_five_times_high():
    assert ".trigger-action-stack > .trigger-action-button" in CSS

    block = CSS.split(
        ".trigger-action-stack > .trigger-action-button",
        1,
    )[1].split("}", 1)[0]

    expected = "calc(var(--btn-h) * 1.5)"

    assert f"height: {expected};" in block
    assert f"min-height: {expected};" in block
    assert f"flex: 0 0 {expected};" in block


def test_dry_run_now_is_above_existing_dry_run():
    now_index = HTML.index('id="btn-dryrun-now"')
    old_index = HTML.index('id="btn-dryrun"')

    assert now_index < old_index
    assert "🧪 DRY-RUN NOW" in HTML
    assert 'onclick="startDryRunNow()"' in HTML


def test_dry_run_buttons_follow_start_lock():
    assert (
        "const btnDryRun    = "
        "document.getElementById('btn-dryrun');"
    ) in JS

    assert (
        "const btnDryRunNow = "
        "document.getElementById('btn-dryrun-now');"
    ) in JS

    assert "const triggerStartLocked = (phase !== 'idle');" in JS

    assert (
        "if (btnDryRun)    "
        "btnDryRun.disabled    = triggerStartLocked;"
    ) in JS

    assert (
        "if (btnDryRunNow) "
        "btnDryRunNow.disabled = triggerStartLocked;"
    ) in JS


def test_dry_run_now_frontend_uses_dedicated_endpoint():
    assert "async function startDryRunNow()" in JS
    assert "fetch('/api/trigger/dryrun_now'" in JS

    # Existing DRY-RUN ×1 remains on its historical endpoint.
    old_block = JS.split(
        "async function startDryRun()",
        1,
    )[1].split(
        "async function stopTrigger()",
        1,
    )[0]

    assert "fetch('/api/trigger/dryrun'" in old_block
    assert "dryrun_now" not in old_block


def test_dry_run_now_api_is_distinct_from_old_dry_run():
    assert (
        '@app.route("/api/trigger/dryrun_now", methods=["POST"])'
        in APP
    )
    assert "def api_trigger_dryrun_now():" in APP
    assert "dry_run_now=True" in APP

    assert (
        '@app.route("/api/trigger/dryrun", methods=["POST"])'
        in APP
    )


def test_service_freezes_dry_run_now_start_at_now_plus_60_seconds(
    tmp_path,
    monkeypatch,
):
    service, state, logs, _events = make_service(tmp_path)

    fixed_now = datetime(
        2026,
        9,
        7,
        22,
        30,
        0,
        tzinfo=timezone.utc,
    )

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    monkeypatch.setattr(
        trigger_service_module,
        "datetime",
        FrozenDateTime,
    )

    plan_path = tmp_path / "configs" / "execution.plan"
    plan_path.write_text("test\n", encoding="utf-8")

    monkeypatch.setattr(
        service,
        "validate_start",
        lambda rig_id=1, require_gps=True: {},
    )
    monkeypatch.setattr(
        service,
        "_resolve_execution_plan",
        lambda rig_id=1: plan_path,
    )

    threads = []

    class FakeThread:
        def __init__(
            self,
            *,
            target,
            args,
            name,
            daemon,
        ):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            self.started = False
            threads.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(
        trigger_service_module.threading,
        "Thread",
        FakeThread,
    )

    assert service.start(
        rig_id=1,
        dry_run_now=True,
    ) is True

    assert len(threads) == 1
    assert threads[0].started is True

    # _run positional contract:
    # simulate, speed, dry_run, delay,
    # dry_run_now, dry_run_now_start_utc, ...
    args = threads[0].args

    assert args[2] is False
    assert args[4] is True
    assert args[5] == "2026-09-07T22:31:00.000Z"

    assert any(
        patch.get("mode") == "dryrun_now"
        for _rig_id, patch in state.trigger_updates
    )

    assert any(
        "UTC now + 60s" in message
        for message, _level, _source in logs
    )


def test_service_passes_only_dry_run_now_cli_flag(
    tmp_path,
    monkeypatch,
):
    service, _state, _logs, _events = make_service(tmp_path)

    circumstances = tmp_path / "circumstances.json"
    circumstances.write_text("{}", encoding="utf-8")

    plan_path = tmp_path / "execution.plan"
    plan_path.write_text("test\n", encoding="utf-8")

    service._active_circumstances_paths[1] = circumstances

    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
            self.stdout = io.StringIO("")
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self):
            self.returncode = 0
            return 0

    monkeypatch.setattr(
        trigger_service_module.subprocess,
        "Popen",
        FakePopen,
    )

    target = "2026-09-07T22:31:00.000Z"

    service._run(
        dry_run_now=True,
        dry_run_now_start_utc=target,
        execution_plan_path=plan_path,
        rig_id=1,
    )

    cmd = captured["cmd"]

    assert "--dry-run-now-start" in cmd
    index = cmd.index("--dry-run-now-start")
    assert cmd[index + 1] == target

    # Critical: DRY-RUN NOW must not activate the historical
    # date-only dry-run branch.
    assert "--dry-run" not in cmd


def test_execution_plan_rebase_never_modifies_source_file(
    tmp_path,
):
    source_path = tmp_path / "reference.plan.json"

    source = {
        "schema_version": 2,
        "config_type": "execution_plan",
        "sequence_start_utc": "2027-08-02T10:00:00.000Z",
        "sequence_end_utc": "2027-08-02T10:02:00.000Z",
        "commands": [
            {
                "time_utc": "2027-08-02T10:00:10.000Z",
                "rig_id": 1,
                "action": "SET",
                "params": {
                    "parameter": "iso",
                    "value": "100",
                },
            },
            {
                "time_utc": "2027-08-02T10:01:00.000Z",
                "rig_id": 1,
                "action": "PHOTO",
                "params": {
                    "expected_frames": 1,
                },
            },
        ],
    }

    source_path.write_text(
        json.dumps(source, indent=2) + "\n",
        encoding="utf-8",
    )

    bytes_before = source_path.read_bytes()

    loaded = load_execution_plan(source_path)
    loaded_before = deepcopy(loaded)

    rebased = rebase_execution_plan(
        loaded,
        datetime(2026, 9, 7, 22, 31, 0),
    )

    # The in-memory source object is not mutated.
    assert loaded == loaded_before

    # More importantly, the reference file is bit-for-bit unchanged.
    assert source_path.read_bytes() == bytes_before

    assert (
        rebased["sequence_start_utc"]
        == "2026-09-07T22:31:00.000Z"
    )

    assert (
        rebased["commands"][0]["time_utc"]
        == "2026-09-07T22:31:10.000Z"
    )

    assert (
        rebased["commands"][1]["time_utc"]
        == "2026-09-07T22:32:00.000Z"
    )


def test_runtime_uses_one_identical_delta_for_timeline_and_plan():
    timeline_block = TRIGGER_SCRIPT.split(
        "_timeline = build_timeline",
        1,
    )[1].split(
        "try:\n    _rig_configuration",
        1,
    )[0]

    assert "_dry_run_now_delta" in timeline_block
    assert (
        "dry_run_now_start\n"
        "        - original_start"
    ) in timeline_block
    assert "rebase_timeline(" in timeline_block

    runtime_block = TRIGGER_SCRIPT.split(
        "def _run_execution_plan_v2():",
        1,
    )[1].split(
        "\ndef main():",
        1,
    )[0]

    assert "_dry_run_now_delta" in runtime_block
    assert "source_sequence_start" in runtime_block
    assert "target_sequence_start" in runtime_block
    assert "rebase_execution_plan(" in runtime_block

    # The runtime path only loads/rebases the source plan.
    # It must never persist the derived plan.
    assert ".write_text(" not in runtime_block
    assert ".write_bytes(" not in runtime_block

    # Historical DRY-RUN ×1 remains a separate branch.
    assert "elif args.dry_run:" in runtime_block
    assert (
        "date UTC remplacée par aujourd'hui"
        in runtime_block
    )


def test_incompatible_trigger_modes_are_rejected(
    tmp_path,
):
    service, _state, _logs, _events = make_service(tmp_path)

    with pytest.raises(
        TriggerValidationError,
        match="mutuellement exclusifs",
    ):
        service.start(
            simulate=True,
            dry_run_now=True,
        )

    with pytest.raises(
        TriggerValidationError,
        match="mutuellement exclusifs",
    ):
        service.start(
            dry_run=True,
            dry_run_now=True,
        )
