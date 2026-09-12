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
def test_totality_override_does_not_replace_selected_inputs(
    tmp_path,
):
    service, _logs, _emits = _service(tmp_path)

    service._procs[3] = _Process()

    selected = tmp_path / "circumstances.json"
    service._active_circumstances_paths[3] = selected

    assert service.override_totality(rig_id=3) is True

    assert service._procs[3].signals == [
        signal.SIGUSR1
    ]
    assert service._active_circumstances_paths[3] == selected


# ---------------------------------------------------------------------------
# REAL / DRY-RUN INPUT PARITY
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


def test_real_and_dryrun_pass_the_same_three_sources_to_runtime(
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
    photo = tmp_path / "configs" / "photo_cfg" / "photo.json"
    exposure = tmp_path / "configs" / "exposure_opt" / "expo.json"

    service._active_circumstances_paths[1] = circumstances
    service._active_photo_paths[1] = photo
    service._active_exposure_opt_paths[1] = exposure

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
        rig_id=1,
    )

    # _run() correctly cleans the active circumstances path at exit.
    # Restore the same source for the second execution.
    service._active_circumstances_paths[1] = circumstances
    service._active_photo_paths[1] = photo
    service._active_exposure_opt_paths[1] = exposure

    # DRY-RUN
    service._run(
        simulate=False,
        dry_run=True,
        rig_id=1,
    )

    assert len(commands) == 2

    real_command = commands[0]
    dryrun_command = commands[1]

    assert "--dry-run" not in real_command
    assert "--dry-run" in dryrun_command
    for flag, path in (("--file", circumstances), ("--camera", photo), ("--exposure-opt", exposure)):
        assert real_command[real_command.index(flag) + 1] == str(path)
        assert dryrun_command[dryrun_command.index(flag) + 1] == str(path)
    assert "--execution-plan" not in real_command + dryrun_command


def test_dryrun_does_not_insert_runtime_wait_for_mechanical_vibration():
    """The phase runtime does not carry legacy mechanical-vibration timing."""

    import inspect
    import scripts.eclipse_trigger as eclipse_trigger

    source = inspect.getsource(eclipse_trigger)

    assert "mechanical_vibration_delay_s" not in source
    assert "mechanical_vibration_enabled" not in source
