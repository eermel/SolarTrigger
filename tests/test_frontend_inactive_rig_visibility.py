from tests.frontend_source import frontend_source
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

HTML = frontend_source()


def test_shared_operational_active_rule_exists():
    assert (
        "function rigIsOperationallyActive(rig)"
        in HTML
    )

    assert (
        "rigId === 1 ||"
        in HTML
    )

    assert (
        "rig.enabled === true"
        in HTML
    )


def test_configuration_rigs_remain_visible_but_dimmed():
    assert (
        ".rig-column:not(.enabled) { opacity: .55; }"
        in HTML
    )

    assert (
        ".camcfg-rig-column:not(.enabled)"
        in HTML
    )

    # Exposure Optimization remains visible even when inactive.
    assert (
        "cameraColumn.hidden = false;"
        in HTML
    )


def test_camera_hides_inactive_rigs():
    assert (
        "cameraRigColumn.hidden = !triggerEnabled;"
        in HTML
    )


def test_trigger_only_exposes_active_rigs():
    assert (
        "const available = rigIsOperationallyActive(rig);"
        in HTML
    )

    assert (
        "if (!rigIsOperationallyActive(rig)) return;"
        in HTML
    )


def test_controls_only_exposes_active_rigs():
    assert (
        "function selectedControlsRig()"
        in HTML
    )

    assert (
        "rigIsOperationallyActive(rig)"
        in HTML
    )

    assert (
        "if (!rigIsOperationallyActive(rig)) {"
        in HTML
    )


def test_selection_falls_back_to_first_active_rig():
    assert (
        "function firstOperationalRigId()"
        in HTML
    )

    assert (
        "selectedTriggerRigId = firstOperationalRigId();"
        in HTML
    )

    assert (
        "selectedRigId = firstOperationalRigId();"
        in HTML
    )


def test_hidden_attribute_cannot_be_overridden_by_button_display_css():
    assert (
        "[hidden] {"
        in HTML
    )
    assert (
        "display: none !important;"
        in HTML
    )
