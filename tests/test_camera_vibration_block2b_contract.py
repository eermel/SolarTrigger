from pathlib import Path
import signal

import pytest

from backend.trigger_service import TriggerService


# ---------------------------------------------------------------------------
# TOTALITY OVERRIDE CONTRACT
# ---------------------------------------------------------------------------


class _State:
    def __init__(self):
        self.updates = []

    def update_trigger_rig(self, rig_id, data):
        self.updates.append(
            (
                rig_id,
                dict(data),
            )
        )


class _Process:
    def __init__(self):
        self.signals = []

    def poll(self):
        return None

    def send_signal(self, value):
        self.signals.append(value)


def _service(tmp_path):
    logs = []
    emits = []

    service = TriggerService(
        _State(),
        tmp_path / "scripts" / "eclipse_trigger.py",
        tmp_path / "dummy.json",
        tmp_path / "configs",
        log_fn=lambda *args: logs.append(args),
        emit_fn=lambda *args: emits.append(args),
        camera_runtime=None,
        rig_config_loader=None,
    )

    return service, logs, emits


@pytest.mark.skipif(
    not hasattr(signal, "SIGUSR1"),
    reason="SIGUSR1 is required by Totality Override",
)
def test_totality_override_only_signals_selected_running_rig(
    tmp_path,
):
    service, _logs, _emits = _service(tmp_path)

    rig1 = _Process()
    rig2 = _Process()

    service._procs[1] = rig1
    service._procs[2] = rig2

    assert service.override_totality(rig_id=2) is True

    assert rig1.signals == []
    assert rig2.signals == [signal.SIGUSR1]

    assert service.state.updates[-1] == (
        2,
        {
            "phase": "totality_override",
        },
    )


@pytest.mark.skipif(
    not hasattr(signal, "SIGUSR1"),
    reason="SIGUSR1 is required by Totality Override",
)
def test_totality_override_does_not_resolve_or_recompile_plan(
    tmp_path,
    monkeypatch,
):
    service, _logs, _emits = _service(tmp_path)

    service._procs[3] = _Process()

    def forbidden_plan_resolution(*_args, **_kwargs):
        raise AssertionError(
            "Totality Override must reuse the already-running .plan"
        )

    monkeypatch.setattr(
        service,
        "_resolve_execution_plan",
        forbidden_plan_resolution,
    )

    assert service.override_totality(rig_id=3) is True

    assert service._procs[3].signals == [
        signal.SIGUSR1
    ]


# ---------------------------------------------------------------------------
# REAL / DRY-RUN EXECUTION PLAN PARITY
# ---------------------------------------------------------------------------


class _Stdout:
    def readline(self):
        return ""


class _FinishedProcess:
    def __init__(self, command):
        self.command = list(command)
        self.stdout = _Stdout()
        self.returncode = 0

    def poll(self):
        return 0

    def wait(self, *args, **kwargs):
        return 0


def _execution_plan_argument(command):
    index = command.index("--execution-plan")
    return command[index + 1]


def test_real_and_dryrun_pass_the_same_plan_to_runtime(
    tmp_path,
    monkeypatch,
):
    service, _logs, _emits = _service(tmp_path)

    circumstances = (
        tmp_path
        / "configs"
        / "circumstances"
        / "eclipse.json"
    )
    plan = (
        tmp_path
        / "configs"
        / "execution_plan"
        / "rig1.plan"
    )

    service._active_circumstances_paths[1] = circumstances

    commands = []

    def fake_popen(command, **_kwargs):
        commands.append(list(command))
        return _FinishedProcess(command)

    monkeypatch.setattr(
        "backend.trigger_service.subprocess.Popen",
        fake_popen,
    )

    # REAL
    service._run(
        simulate=False,
        dry_run=False,
        dry_run_now=False,
        execution_plan_path=plan,
        rig_id=1,
    )

    # _run() correctly cleans the active circumstances path at exit.
    # Restore the same source for the second execution.
    service._active_circumstances_paths[1] = circumstances

    # DRY-RUN
    service._run(
        simulate=False,
        dry_run=True,
        dry_run_delay=30.0,
        dry_run_now=False,
        execution_plan_path=plan,
        rig_id=1,
    )

    assert len(commands) == 2

    real_command = commands[0]
    dryrun_command = commands[1]

    assert _execution_plan_argument(
        real_command
    ) == str(plan)

    assert _execution_plan_argument(
        dryrun_command
    ) == str(plan)

    assert "--dry-run" not in real_command
    assert "--dry-run" in dryrun_command

    # The mode changes timeline execution only.
    # It must never select or compile another plan.
    assert (
        _execution_plan_argument(real_command)
        == _execution_plan_argument(dryrun_command)
    )


def test_dryrun_does_not_insert_runtime_wait_for_mechanical_vibration():
    """Mechanical settling belongs to compiled timestamps, never runtime sleep."""

    import backend.execution_plan_runtime as execution_plan_runtime
    import inspect

    source = inspect.getsource(execution_plan_runtime)

    assert "mechanical_vibration_delay_s" not in source
    assert "mechanical_vibration_enabled" not in source
