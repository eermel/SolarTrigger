import pytest

from backend.trigger_command_coordinator import (
    TriggerCommandBusyError,
    TriggerCommandCoordinator,
)


def test_stop_cancels_only_selected_pending_rig():
    coordinator = TriggerCommandCoordinator()
    command = coordinator.begin((1, 2), "real")

    assert coordinator.cancel_rig(2) is True
    assert command.is_rig_cancelled(1) is False
    assert command.is_rig_cancelled(2) is True
    assert command.cancelled_rig_ids() == (2,)

    coordinator.finish(command)
    assert coordinator.busy() is False


def test_emergency_aborts_pending_batch_and_blocks_new_start_until_release():
    coordinator = TriggerCommandCoordinator()
    command = coordinator.begin((1, 2), "debug")

    preempted = coordinator.begin_emergency()

    assert preempted is command
    assert command.aborted is True
    with pytest.raises(TriggerCommandBusyError):
        coordinator.begin((3,), "real")

    coordinator.finish(command)
    assert coordinator.busy() is True

    coordinator.finish_emergency()
    assert coordinator.busy() is False


def test_second_emergency_is_coalesced_by_priority_gate():
    coordinator = TriggerCommandCoordinator()
    assert coordinator.begin_emergency() is None
    with pytest.raises(TriggerCommandBusyError):
        coordinator.begin_emergency()
    coordinator.finish_emergency()
