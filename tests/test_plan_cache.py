from backend.plan_cache import RigPlanCache, rig_plan_version


def test_rig_plan_version_is_deterministic_and_changes_with_iso_max():
    policy = {
        "photo": {"iso_max": 1600, "motion_tolerance_px": 2.0},
        "optics": {"focal_length_mm": 400},
    }
    reordered = {
        "optics": {"focal_length_mm": 400},
        "photo": {"motion_tolerance_px": 2.0, "iso_max": 1600},
    }
    changed = {
        "photo": {"iso_max": 3200, "motion_tolerance_px": 2.0},
        "optics": {"focal_length_mm": 400},
    }

    assert rig_plan_version(policy) == rig_plan_version(reordered)
    assert rig_plan_version(policy) != rig_plan_version(changed)


def test_rig_plan_version_ignores_fields_outside_version_input():
    first = {"photo": {"iso_max": 1600}, "unrelated": "first"}
    second = {"photo": {"iso_max": 1600}, "unrelated": "second"}

    assert rig_plan_version(first) == rig_plan_version(second)


def test_version_change_clears_only_that_rig_entries():
    cache = RigPlanCache()
    first_value = {
        "augmented_intent": {"iso": 800},
        "iso_applied": 800,
        "corrections": [],
        "warnings": [],
    }
    other_value = {"augmented_intent": {"iso": 400}}

    assert cache.set_version_and_clear_if_changed("rig-1", "version-1") is True
    cache.put("rig-1", "intent-1", first_value)
    cache.put("rig-2", "intent-1", other_value)

    assert cache.set_version_and_clear_if_changed("rig-1", "version-1") is False
    assert cache.get("rig-1", "intent-1") is first_value

    assert cache.set_version_and_clear_if_changed("rig-1", "version-2") is True
    assert cache.get_version("rig-1") == "version-2"
    assert cache.get("rig-1", "intent-1") is None
    assert cache.get("rig-2", "intent-1") is other_value


def test_clear_removes_rig_version_and_entries():
    cache = RigPlanCache()
    cache.set_version_and_clear_if_changed("rig-1", "version-1")
    cache.put("rig-1", "intent-1", {"iso_applied": 800})

    cache.clear("rig-1")

    assert cache.get("rig-1", "intent-1") is None
    assert cache.get_version("rig-1") is None
