from services.camera_service import CaptureIntent
from plugins.camera.sony import SonyPlugin


def _intent(plan):
    from datetime import datetime

    return CaptureIntent(
        shutter_min=None,
        shutter_max=None,
        step_ev=None,
        speeds=None,
        phase="totality",
        target_time=datetime(2027, 8, 2, 12, 0, 0),
        deadline=None,
        overflow_policy=None,
        origin="sequencer",
        request_id="audit",
        exposure_plan=plan,
    )


def test_sony_native_bracket_audit_exposes_real_commands():
    plugin = SonyPlugin(None, lambda *_args, **_kwargs: None)

    plan = [
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 100},
        {"shutter": "1/125", "iso": 100},
        {"shutter": "1/60", "iso": 100},
    ]

    prepared = plugin.prepare_capture(_intent(plan))
    operations = plugin.audit_prepared_capture(prepared)

    assert prepared.token[0] == "sony_exposure_sequence"

    assert operations[0] == {
        "action": "set",
        "parameter": "iso",
        "value": "100",
    }

    assert operations[1] == {
        "action": "set",
        "parameter": "capturemode",
        "value": "Single Shot",
    }

    assert operations[2] == {
        "action": "set",
        "parameter": "shutterspeed",
        "value": "1/250",
    }

    assert operations[3]["action"] == "set"
    assert operations[3]["parameter"] == "capturemode"
    assert operations[3]["value"] == "Continuous Bracket 1.0 EV 5 Img."

    press = operations[4]
    assert press["action"] == "bracket_press"
    assert press["parameter"] == "bulb"
    assert press["value"] == "1"
    assert press["centre"] == "1/250"
    assert press["step_ev"] == 1.0
    assert press["frames"] == 5
    assert press["physical_views"] == [
        "1/1000",
        "1/500",
        "1/250",
        "1/125",
        "1/60",
    ]

    assert operations[5] == {
        "action": "expect_frames",
        "count": 5,
        "physical_views": [
            "1/1000",
            "1/500",
            "1/250",
            "1/125",
            "1/60",
        ],
    }

    assert operations[6] == {
        "action": "bracket_release",
        "parameter": "bulb",
        "value": "0",
    }

    assert operations[7] == {
        "action": "settle_idle",
    }


def test_sony_mixed_audit_keeps_bracket_and_variable_iso_singles():
    plugin = SonyPlugin(None, lambda *_args, **_kwargs: None)

    plan = [
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 100},
        {"shutter": "1/125", "iso": 100},
        {"shutter": "1/60", "iso": 100},

        {"shutter": "1/60", "iso": 200},
        {"shutter": "1/60", "iso": 400},
    ]

    prepared = plugin.prepare_capture(_intent(plan))
    operations = plugin.audit_prepared_capture(prepared)

    assert prepared.token[0] == "sony_exposure_mixed"

    capture_modes = [
        op["value"]
        for op in operations
        if op.get("action") == "set"
        and op.get("parameter") == "capturemode"
    ]

    assert "Continuous Bracket 1.0 EV 5 Img." in capture_modes

    singles = [
        op
        for op in operations
        if op.get("action") == "trigger_capture"
    ]

    assert [
        (op["shutter"], op["iso"])
        for op in singles
    ] == [
        ("1/60", 200),
        ("1/60", 400),
    ]
