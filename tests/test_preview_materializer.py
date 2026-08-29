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

    assert materializer.apply_atmos_if_enabled({"photo": {"atmos_enabled": False}}, plan, target, {}) == (plan, False)
    updated, added = materializer.apply_atmos_if_enabled({"photo": {"atmos_enabled": True}}, plan, target, lambda: context)
    assert added is True
    assert updated[2] == "0.032"
    with pytest.raises(materializer.PreviewMaterializationError, match="incomplete") as error:
        materializer.apply_atmos_if_enabled({"photo": {"atmos_enabled": True}}, plan, target, {})
    assert error.value.code == "CONFIG_INVALID"


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
