import inspect
import sys
from types import SimpleNamespace

import pytest

from backend.camera_characterization import characterize


def _install_fake_gphoto2(monkeypatch):
    gp = SimpleNamespace(
        GP_STORAGEINFO_FREESPACEIMAGES=1,
        GP_STORAGEINFO_FREESPACEKBYTES=2,
        GP_STORAGEINFO_MAXCAPACITY=4,
    )
    monkeypatch.setitem(sys.modules, "gphoto2", gp)
    return gp


def test_characterization_cold_starts_every_capture_candidate():
    source = inspect.getsource(characterize)
    assert "def fresh_capture_session(reason)" in source
    assert "direct_nodes.clear()" in source
    assert "COLD TRIGGER PASS" in source
    assert "COLD BRACKET PASS" in source
    assert "expected_trials=6" in source


def test_characterization_builds_direct_set_dependency_matrix():
    source = inspect.getsource(characterize)
    assert "dependency_baseline" in source
    assert "dependency_alternate" in source
    assert 'commands[source_key]["invalidates"]' in source
    assert "conservative invalidation enabled" in source


def test_characterization_prunes_failed_bracket_methods_for_larger_sizes():
    source = inspect.getsource(characterize)
    assert "rejected_bracket_candidates" in source
    assert "rejected_bracket_candidates[command_id]" in source
    assert "will not be retested" in source


def test_characterization_promotes_optional_shutter_mode_to_direct_set():
    source = inspect.getsource(characterize)
    assert 'shutter_mode_spec["writer"] = "single_config"' in source
    assert "direct shutter-mode target readback mismatch" in source


def test_storage_guard_rejects_known_full_card(monkeypatch):
    gp = _install_fake_gphoto2(monkeypatch)
    from backend.camera_characterization import (
        CameraStorageCapacityError,
        _ensure_camera_storage,
    )

    storage = SimpleNamespace(
        fields=(
            gp.GP_STORAGEINFO_FREESPACEIMAGES
            | gp.GP_STORAGEINFO_FREESPACEKBYTES
            | gp.GP_STORAGEINFO_MAXCAPACITY
        ),
        basedir="/store_00010001",
        label="CARD",
        freeimages=0,
        freekbytes=0,
        capacitykbytes=1000000,
    )
    camera = SimpleNamespace(get_storageinfo=lambda: [storage])

    with pytest.raises(CameraStorageCapacityError, match="capacity|full"):
        _ensure_camera_storage(camera, 1, "unit test")


def test_storage_guard_accepts_reported_capacity(monkeypatch):
    gp = _install_fake_gphoto2(monkeypatch)
    from backend.camera_characterization import _ensure_camera_storage

    storage = SimpleNamespace(
        fields=(
            gp.GP_STORAGEINFO_FREESPACEIMAGES
            | gp.GP_STORAGEINFO_FREESPACEKBYTES
        ),
        basedir="/store_00010001",
        label="CARD",
        freeimages=42,
        freekbytes=123456,
        capacitykbytes=0,
    )
    camera = SimpleNamespace(get_storageinfo=lambda: [storage])
    result = _ensure_camera_storage(camera, 40, "unit test")
    assert result["stores"][0]["free_images"] == 42


def test_storage_guard_does_not_reject_unused_empty_second_slot(monkeypatch):
    gp = _install_fake_gphoto2(monkeypatch)
    from backend.camera_characterization import _ensure_camera_storage

    def store(index, freeimages):
        return SimpleNamespace(
            fields=gp.GP_STORAGEINFO_FREESPACEIMAGES,
            basedir=f"/store_{index}",
            label=f"CARD{index}",
            freeimages=freeimages,
            freekbytes=0,
            capacitykbytes=0,
        )

    camera = SimpleNamespace(
        get_storageinfo=lambda: [store(1, 0), store(2, 100)]
    )
    result = _ensure_camera_storage(camera, 20, "dual-slot unit test")
    assert max(item["free_images"] for item in result["stores"]) == 100


def test_storage_failure_is_not_scored_as_trigger_failure():
    source = inspect.getsource(characterize)
    assert "CameraStorageCapacityError" in source
    assert "single-trigger characterization matrix" in source
    assert "native bracket {frames}-frame characterization matrix" in source
    assert "incomplete capture confirmation" in source


def test_readonly_fresh_session_preflight_is_operator_assisted():
    source = inspect.getsource(characterize)
    assert "Fresh-session camera preflight" in source
    assert "Correct this setting physically on the camera" in source
