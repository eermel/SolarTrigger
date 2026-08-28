from datetime import datetime, timezone

from backend.exposure_model import LogicalExposureRequest, MaterializedExposure


def test_exposure_models_can_be_instantiated():
    target_time = datetime(2026, 8, 12, 17, 30, tzinfo=timezone.utc)
    deadline = datetime(2026, 8, 12, 17, 31, tzinfo=timezone.utc)

    logical_request = LogicalExposureRequest(
        shutter_min="1/1000",
        shutter_max="1/125",
        step_ev=1.0,
        speeds=["1/1000", "1/500", "1/250", "1/125"],
        iso_target="100",
        phase="C2",
        target_time=target_time,
        deadline=deadline,
        origin="scheduler",
        request_id="request-1",
    )
    materialized = MaterializedExposure(
        rig_id=1,
        plugin_name="simulated",
        exposures_s=[0.001, 0.002, 0.004, 0.008],
        iso_applied="100",
        corrections=["shutter rounded"],
        warnings=["deadline close"],
        logical_request_id="request-1",
    )

    assert logical_request.target_time == target_time
    assert logical_request.deadline == deadline
    assert materialized.logical_request_id == logical_request.request_id


def test_exposure_models_accept_minimal_optional_values():
    logical_request = LogicalExposureRequest(
        shutter_min=None,
        shutter_max=None,
        step_ev=None,
        speeds=None,
        iso_target=None,
        phase="partial",
        target_time=datetime(2026, 8, 12, tzinfo=timezone.utc),
        deadline=None,
    )
    materialized = MaterializedExposure(
        rig_id=2,
        plugin_name="simulated",
        exposures_s=None,
    )

    assert logical_request.origin is None
    assert logical_request.request_id is None
    assert materialized.iso_applied is None
    assert materialized.corrections == []
    assert materialized.warnings == []
    assert materialized.logical_request_id is None
