from datetime import datetime, timedelta

import pytest

from backend import preview_materializer as materializer


def test_normalize_intent_plan_distinguishes_regular_and_irregular_lists():
    regular = materializer.normalize_intent_plan({"speeds": ["1/500", "1/1000", "1/2000"]})
    irregular = materializer.normalize_intent_plan({"speeds": ["1/1000", "1/500", "1/60"]})

    assert regular == (True, "1/2000", "1/500", 1.0, None)
    assert irregular[:4] == (False, "1/1000", "1/60", pytest.approx(2.0294468445))
    assert irregular[4] == ["1/1000", "1/500", "1/60"]


def test_atmos_is_per_rig_and_requires_complete_context(monkeypatch):
    target = datetime(2026, 8, 12, 12)
    timeline = {
        "C1": target - timedelta(hours=2), "C2": target - timedelta(hours=1),
        "TMAX": target, "C3": target + timedelta(hours=1),
        "C4": target + timedelta(hours=2),
    }
    context = {"timeline": timeline, "altitudes": {f"{key}_alt_deg": 20 for key in timeline}, "location": {"altitude_m": 0}}
    plan = (True, "1/1000", "1/125", 1.0, None)
    monkeypatch.setattr(materializer, "facteur_atmospherique", lambda _h, _alt: 4.0)

    assert materializer.apply_atmos_if_enabled(
        {"photo": {"atmos_enabled": False}}, plan, target, {}
    ) == (plan, False, None)

    updated, added, theoretical = materializer.apply_atmos_if_enabled(
        {"photo": {"atmos_enabled": True}},
        plan,
        target,
        lambda: context,
    )

    assert added is True
    assert theoretical is None
    assert updated[2] == "1/31.25"
    with pytest.raises(materializer.PreviewMaterializationError, match="incomplete") as error:
        materializer.apply_atmos_if_enabled({"photo": {"atmos_enabled": True}}, plan, target, {})
    assert error.value.code == "CONFIG_INVALID"


def test_preview_atmos_rejects_placeholder_equal_contacts():
    target = datetime(2027, 2, 6, 0, 0, 0)
    timeline = {
        "C1": target,
        "C2": target,
        "TMAX": target,
        "C3": target,
        "C4": target,
    }
    context = {
        "timeline": timeline,
        "altitudes": {
            "C1_alt_deg": 0.0,
            "C2_alt_deg": 0.0,
            "TMAX_alt_deg": 0.0,
            "C3_alt_deg": 0.0,
            "C4_alt_deg": 0.0,
        },
        "location": {"altitude_m": 79.0},
    }
    plan = (True, "1/2000", "1/500", 1.0, None)

    with pytest.raises(
        materializer.PreviewMaterializationError,
        match="invalid",
    ):
        materializer.apply_atmos_if_enabled(
            {"photo": {"atmos_enabled": True}},
            plan,
            target,
            context,
        )


def test_iso_diagnostics_are_ordered_and_deduplicated(monkeypatch):
    monkeypatch.setattr(materializer, "safe_shutter_and_iso", lambda *args, **kwargs: {
        "iso": 400,
        "corrections": ["iso_compensated", "iso_rounded", "iso_compensated"],
        "warnings": ["iso_capped", "iso_capped"],
    })
    assert materializer.compute_iso_and_corrections("100", "1/8", {"iso_max": 400}) == (
        "400", ["iso_compensated", "iso_rounded"], ["iso_capped"]
    )


def test_policy_mapping_and_exposure_assembly():
    rig = {"photo": {"anti_trailing_enabled": True}, "devices": {"mount": {"control": "indi", "geometry": "altaz", "tracking": "solar"}}}
    assert materializer.resolve_policy(rig) == "field_rotation"
    assert materializer.assemble_exposures_s((True, "1/8", "1/2", 1.0, None)) == [0.125, 0.25, 0.5]
    assert materializer.assemble_exposures_s((False, "1/1000", "1/60", 2.0, ["1/1000", "1/500", "1/60"])) == pytest.approx([0.001, 0.002, 1 / 60])


def test_atmospheric_extension_adds_ev_steps_without_iso_compensation(monkeypatch):
    target = datetime(2026, 8, 12, 12)
    timeline = {
        "C1": target - timedelta(hours=2),
        "C2": target - timedelta(hours=1),
        "TMAX": target,
        "C3": target + timedelta(hours=1),
        "C4": target + timedelta(hours=2),
    }
    context = {
        "timeline": timeline,
        "altitudes": {f"{key}_alt_deg": 20 for key in timeline},
        "location": {"altitude_m": 0},
    }
    plan = (True, "1/1000", "1/125", 1.0, None)

    monkeypatch.setattr(
        materializer,
        "facteur_atmospherique",
        lambda _h, _alt: 4.0,
    )

    updated, applied, theoretical = materializer.apply_atmos_if_enabled(
        {"photo": {"atmos_enabled": True}},
        plan,
        target,
        context,
    )

    assert applied is True
    assert theoretical is None

    # Runtime parity:
    # 1/125 -> 1/62.5 -> 1/31.25 for a 4x attenuation factor.
    assert updated == (
        True,
        "1/1000",
        "1/31.25",
        1.0,
        None,
    )

    exposures = materializer.assemble_exposures_s(updated)

    assert exposures == pytest.approx([
        1 / 1000,
        1 / 500,
        1 / 250,
        1 / 125,
        1 / 62.5,
        1 / 31.25,
    ])


def test_iso_compensation_switch_is_forwarded_to_safe_selection(monkeypatch):
    calls = []

    def fake_safe(*args, **kwargs):
        calls.append(kwargs)
        return {
            "shutter": "1/125",
            "iso": 200,
            "corrections": [],
            "warnings": [],
        }

    monkeypatch.setattr(
        materializer,
        "safe_shutter_and_iso",
        fake_safe,
    )

    materializer.compute_iso_and_corrections(
        200,
        "1/125",
        {
            "iso_max": 6400,
            "iso_compensation_enabled": False,
        },
    )

    assert calls[0]["iso_compensation_enabled"] is False


def test_exposure_diff_only_reports_actual_changes():
    lines = materializer.build_exposure_diff_lines(
        ["1/1000", "1/500", "1/250"],
        100,
        ["1/1000", "1/500", "1/250"],
        100,
    )

    assert lines == []


def test_exposure_diff_reports_iso_change_and_added_pose():
    lines = materializer.build_exposure_diff_lines(
        ["1/1000", "1/500"],
        100,
        ["1/1000", "1/500", "1/250"],
        200,
    )

    assert lines == [
        "(1/1000 ; 100) → (1/1000 ; 200)",
        "(1/500 ; 100) → (1/500 ; 200)",
        "+ (1/250 ; 200)",
    ]


def test_exposure_diff_uses_per_exposure_iso_plan():
    lines = materializer.build_exposure_diff_lines(
        [
            "1/4000",
            "1/2000",
            "1/1000",
            "1/500",
            "1/250",
            "1/125",
            "1/60",
            "1/30",
            "1/15",
            "1/8",
            "1/4",
            "1/2",
            "1",
            "2",
            "4",
            "8",
        ],
        100,
        [
            "1/4000",
            "1/2000",
            "1/1000",
            "1/500",
            "1/250",
            "1/125",
            "1/60",
            "1/30",
            "1/15",
            "1/8",
            "1/8",
            "1/8",
            "1/8",
            "1/8",
            "1/8",
            "1/8",
        ],
        6400,
        final_isos=[
            100,
            100,
            100,
            100,
            100,
            100,
            100,
            100,
            100,
            100,
            200,
            400,
            800,
            1600,
            3200,
            6400,
        ],
    )

    assert lines == [
        "(1/4 ; 100) → (1/8 ; 200)",
        "(1/2 ; 100) → (1/8 ; 400)",
        "(1 ; 100) → (1/8 ; 800)",
        "(2 ; 100) → (1/8 ; 1600)",
        "(4 ; 100) → (1/8 ; 3200)",
        "(8 ; 100) → (1/8 ; 6400)",
    ]

def test_exposure_diff_reports_removed_slow_pose():
    lines = materializer.build_exposure_diff_lines(
        ["1/1000", "1/500", "1/250"],
        100,
        ["1/1000", "1/500"],
        100,
    )

    assert lines == [
        "- (1/250 ; 100)",
    ]


def test_photo_shutter_formatter_uses_fraction_for_half_second():
    assert materializer.format_photo_shutter("0.5") == "1/2"
    assert materializer.format_photo_shutter("1/60") == "1/60"
    assert materializer.format_photo_shutter("4") == "4"


def test_nikon_executable_preview_uses_real_nikon_grid():
    plan = (
        True,
        "1/4000",
        "4",
        1.0,
        None,
    )
    rig = {
        "devices": {
            "camera": {
                "backend": "nikon-dslr",
            }
        }
    }

    shutters = materializer.expand_executable_shutters(
        rig,
        plan,
    )

    assert shutters[0] == "1/4000"
    assert shutters[-1] == "4"
    assert "1/60" in shutters


def test_sony_executable_preview_uses_real_sony_planner():
    plan = (
        True,
        "1/4000",
        "1/15",
        1.0,
        None,
    )
    rig = {
        "devices": {
            "camera": {
                "backend": "sony",
            }
        }
    }

    shutters = materializer.expand_executable_shutters(
        rig,
        plan,
    )

    _step, _count, sequence = materializer.sony_exposure_planner.plan(
        "1/4000",
        "1/15",
        1.0,
    )
    expected = []
    for item in sequence:
        if isinstance(
            item,
            materializer.sony_exposure_planner.SinglePhoto,
        ):
            expected.append(str(item.speed))
        else:
            expected.extend(str(view) for view in item.views)

    assert shutters == expected



def test_preview_atmos_accepts_partial_eclipse_context(monkeypatch):
    c1 = datetime(2026, 8, 12, 17, 0, 0)
    tmax = datetime(2026, 8, 12, 18, 0, 0)
    c4 = datetime(2026, 8, 12, 19, 0, 0)

    context = {
        "timeline": {
            "C1": c1,
            "TMAX": tmax,
            "C4": c4,
        },
        "altitudes": {
            "C1_alt_deg": 20.0,
            "C2_alt_deg": None,
            "TMAX_alt_deg": 10.0,
            "C3_alt_deg": None,
            "C4_alt_deg": 5.0,
        },
        "location": {"altitude_m": 69.0},
    }

    monkeypatch.setattr(
        materializer,
        "facteur_atmospherique",
        lambda _h, _alt: 4.0,
    )

    plan = (True, "1/2000", "1/500", 1.0, None)

    updated, applied, _ = materializer.apply_atmos_if_enabled(
        {"photo": {"atmos_enabled": True}},
        plan,
        tmax,
        context,
    )

    assert applied is True
    assert updated[2] == "1/125"



def test_preview_atmos_is_ignored_at_30_deg_and_above(monkeypatch):
    target = datetime(2027, 8, 2, 10, 12, 58)

    timeline = {
        "C1": target - timedelta(hours=2),
        "C2": target - timedelta(minutes=3),
        "TMAX": target,
        "C3": target + timedelta(minutes=3),
        "C4": target + timedelta(hours=1),
    }

    context = {
        "timeline": timeline,
        "altitudes": {
            "C1_alt_deg": 30.0,
            "C2_alt_deg": 30.0,
            "TMAX_alt_deg": 30.0,
            "C3_alt_deg": 30.0,
            "C4_alt_deg": 30.0,
        },
        "location": {
            "altitude_m": 4.0,
        },
    }

    plan = (
        True,
        "1/4000",
        "4",
        1.0,
        None,
    )

    def must_not_be_called(*_args, **_kwargs):
        raise AssertionError(
            "facteur_atmospherique must not be called for Sun >= 30 deg"
        )

    monkeypatch.setattr(
        materializer,
        "facteur_atmospherique",
        must_not_be_called,
    )

    updated, applied, theoretical = (
        materializer.apply_atmos_if_enabled(
            {"photo": {"atmos_enabled": True}},
            plan,
            target,
            context,
        )
    )

    assert updated == plan
    assert applied is False
    assert theoretical is None


def test_sony_physical_expansion_can_include_planner_overshoot():
    plan = (
        True,
        "1/1000",
        "1/125",
        1.0,
        None,
    )
    rig = {
        "devices": {
            "camera": {
                "backend": "sony",
            }
        }
    }

    shutters = materializer.expand_executable_shutters(rig, plan)

    assert shutters == [
        "1/1000",
        "1/500",
        "1/250",
        "1/125",
        "1/60",
    ]
