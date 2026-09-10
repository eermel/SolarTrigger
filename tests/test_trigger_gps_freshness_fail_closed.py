from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.trigger_service import TriggerService, TriggerValidationError


class _State:
    def __init__(self, gps):
        self._gps = dict(gps)

    def snapshot(self, key):
        assert key == "gps"
        return dict(self._gps)


class _ReachedPlanResolution(RuntimeError):
    pass


def _service(tmp_path, gps):
    return TriggerService(
        _State(gps),
        tmp_path / "scripts" / "eclipse_trigger.py",
        tmp_path / "todayeclipse.json",
        tmp_path / "configs",
        lambda *_args, **_kwargs: None,
        lambda *_args, **_kwargs: None,
        camera_runtime=object(),
        rig_config_loader=lambda: {},
    )


def _assert_error(service, expected_code):
    with pytest.raises(TriggerValidationError) as caught:
        service.validate_start(require_gps=True)
    assert caught.value.code == expected_code


def test_synced_without_sync_time_fails_closed(tmp_path):
    service = _service(tmp_path, {"synced": True})
    _assert_error(service, "GPS_SYNC_TIME_INVALID")


def test_synced_with_empty_sync_time_fails_closed(tmp_path):
    service = _service(tmp_path, {"synced": True, "sync_time": "   "})
    _assert_error(service, "GPS_SYNC_TIME_INVALID")


def test_synced_with_non_string_sync_time_fails_closed(tmp_path):
    service = _service(tmp_path, {"synced": True, "sync_time": 1_700_000_000})
    _assert_error(service, "GPS_SYNC_TIME_INVALID")


def test_synced_with_malformed_sync_time_fails_closed(tmp_path):
    service = _service(tmp_path, {"synced": True, "sync_time": "not-a-date"})
    with pytest.raises(TriggerValidationError) as caught:
        service.validate_start(require_gps=True)
    assert caught.value.code == "GPS_SYNC_TIME_INVALID"
    assert caught.value.__cause__ is not None


def test_stale_sync_still_fails_with_existing_code(tmp_path):
    sync_time = (
        datetime.now(timezone.utc) - timedelta(hours=3)
    ).isoformat().replace("+00:00", "Z")
    service = _service(tmp_path, {"synced": True, "sync_time": sync_time})
    _assert_error(service, "GPS_SYNC_STALE")


def test_fresh_aware_sync_reaches_plan_resolution(tmp_path):
    sync_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    service = _service(tmp_path, {"synced": True, "sync_time": sync_time})

    def reached(_rig_id):
        raise _ReachedPlanResolution

    service._resolve_execution_plan = reached
    with pytest.raises(_ReachedPlanResolution):
        service.validate_start(require_gps=True)


def test_fresh_naive_sync_keeps_legacy_utc_compatibility(tmp_path):
    sync_time = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    service = _service(tmp_path, {"synced": True, "sync_time": sync_time})

    def reached(_rig_id):
        raise _ReachedPlanResolution

    service._resolve_execution_plan = reached
    with pytest.raises(_ReachedPlanResolution):
        service.validate_start(require_gps=True)


def test_require_gps_false_does_not_add_a_new_gate(tmp_path):
    service = _service(tmp_path, {"synced": False})

    def reached(_rig_id):
        raise _ReachedPlanResolution

    service._resolve_execution_plan = reached
    with pytest.raises(_ReachedPlanResolution):
        service.validate_start(require_gps=False)
