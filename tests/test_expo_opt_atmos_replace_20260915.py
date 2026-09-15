from backend.preview_materializer import apply_atmos_if_enabled

def _rig(replace=False):
    return {
        "photo": {
            "atmos_enabled": True,
            "atmos_replace_enabled": replace,
        },
        "devices": {
            "camera": {
                "manufacturer": "SONY",
                "model": "ILCE-7M5",
            }
        },
    }

def _ctx():
    return {
        "timeline": {
            "C1": "2027-08-02T10:00:00+00:00",
            "C2": "2027-08-02T10:30:00+00:00",
            "TMAX": "2027-08-02T10:31:00+00:00",
            "C3": "2027-08-02T10:32:00+00:00",
            "C4": "2027-08-02T11:00:00+00:00",
        },
        "altitudes": {
            "C1_alt_deg": 10.0,
            "C2_alt_deg": 10.0,
            "TMAX_alt_deg": 10.0,
            "C3_alt_deg": 10.0,
            "C4_alt_deg": 10.0,
        },
        "location": {"altitude_m": 0.0},
    }

def test_atmos_append_mode_keeps_original_and_adds_compensated(monkeypatch):
    monkeypatch.setattr(
        "backend.preview_materializer._shared_expand_executable_shutters",
        lambda _rig, _plan: ["1/1000", "1/500", "1/250"],
    )
    monkeypatch.setattr(
        "backend.preview_materializer.validate_atmospheric_timeline",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "backend.preview_materializer.interpolate_altitude",
        lambda *_a, **_k: 10.0,
    )
    monkeypatch.setattr(
        "backend.preview_materializer.facteur_atmospherique",
        lambda *_a, **_k: 2.0,
    )
    monkeypatch.setattr(
        "backend.preview_materializer.nearest_executable_shutter",
        lambda _rig, seconds: {
            0.002: "1/500",
            0.004: "1/250",
            0.008: "1/125",
        }[round(seconds, 6)],
    )

    plan = (True, "1/1000", "1/250", 1.0, None)
    final_plan, added, _ = apply_atmos_if_enabled(
        _rig(False), plan, object(), _ctx()
    )

    assert added is True
    assert final_plan[4] == [
        "1/1000", "1/500", "1/250",
        "1/500", "1/250", "1/125",
    ]

def test_atmos_replace_mode_uses_only_compensated(monkeypatch):
    monkeypatch.setattr(
        "backend.preview_materializer._shared_expand_executable_shutters",
        lambda _rig, _plan: ["1/1000", "1/500", "1/250"],
    )
    monkeypatch.setattr(
        "backend.preview_materializer.validate_atmospheric_timeline",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "backend.preview_materializer.interpolate_altitude",
        lambda *_a, **_k: 10.0,
    )
    monkeypatch.setattr(
        "backend.preview_materializer.facteur_atmospherique",
        lambda *_a, **_k: 2.0,
    )
    monkeypatch.setattr(
        "backend.preview_materializer.nearest_executable_shutter",
        lambda _rig, seconds: {
            0.002: "1/500",
            0.004: "1/250",
            0.008: "1/125",
        }[round(seconds, 6)],
    )

    plan = (True, "1/1000", "1/250", 1.0, None)
    final_plan, added, _ = apply_atmos_if_enabled(
        _rig(True), plan, object(), _ctx()
    )

    assert added is True
    assert final_plan[4] == ["1/500", "1/250", "1/125"]


def test_rig_defaults_include_atmos_replace_off():
    from backend.rig_config import canonical_rig_defaults
    cfg = canonical_rig_defaults(1, atmos_enabled=True)
    assert cfg["photo"]["atmos_enabled"] is True
    assert cfg["photo"]["atmos_replace_enabled"] is False


def test_expo_opt_ui_has_dependent_replace_switch_and_inactive_rig_save_policy():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    index = (root / "flask_app/templates/index.html").read_text(encoding="utf-8")
    js = (root / "flask_app/static/js/solartrigger.js").read_text(encoding="utf-8")

    assert 'id="cfg-atmo-replace-switch"' in index
    assert "replace actual exposition by compensated expositions" in index
    assert "replace.disabled = !enabled" in js
    assert "if (!enabled) replace.checked = false" in js

    assert "refreshExposureOptRigVisibility" in js
    assert "column.hidden = !_exposureOptRigIsActive(rigId)" in js

    assert "const rigs = [1, 2, 3, 4].map" in js
    assert "active ? current.photo.anti_trailing_enabled : false" in js
    assert "active ? current.photo.mechanical_vibration_enabled : false" in js
    assert "active ? current.photo.iso_compensation_enabled : false" in js
