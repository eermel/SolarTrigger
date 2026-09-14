from datetime import datetime, timedelta, timezone

import pytest

from backend.trigger_service import TriggerService, TriggerValidationError


class _State:
    def __init__(self, gps):
        self.gps = gps

    def snapshot(self, key):
        assert key == "gps"
        return self.gps


class _InputsReached(RuntimeError):
    pass


def _service_with_gps(sync_time):
    service = object.__new__(TriggerService)
    service.state = _State({
        "synced": True,
        "sync_time": sync_time,
    })

    def _inputs(*_args, **_kwargs):
        raise _InputsReached()

    service._resolve_trigger_inputs = _inputs
    return service


def test_gps_sync_timestamp_far_in_future_is_rejected():
    service = _service_with_gps("2999-01-01T00:00:00Z")

    with pytest.raises(TriggerValidationError) as exc_info:
        service.validate_start(rig_id=1, require_gps=True, selected={})

    assert exc_info.value.code == "GPS_SYNC_TIME_INVALID"
    assert "future" in str(exc_info.value).lower()


def test_gps_sync_timestamp_small_future_skew_is_tolerated():
    sync_time = (
        datetime.now(timezone.utc) + timedelta(seconds=2)
    ).isoformat()

    service = _service_with_gps(sync_time)

    with pytest.raises(_InputsReached):
        service.validate_start(rig_id=1, require_gps=True, selected={})


def test_gps_sync_timestamp_older_than_two_hours_stays_rejected():
    sync_time = (
        datetime.now(timezone.utc) - timedelta(hours=2, seconds=10)
    ).isoformat()

    service = _service_with_gps(sync_time)

    with pytest.raises(TriggerValidationError) as exc_info:
        service.validate_start(rig_id=1, require_gps=True, selected={})

    assert exc_info.value.code == "GPS_SYNC_STALE"
