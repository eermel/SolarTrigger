import json

from flask_app.app import app


def _payload(replace_value):
    return {
        "filename": "replace_validation_test",
        "overwrite": True,
        "data": {
            "schema_version": 1,
            "config_type": "exposure_optimization",
            "atmospheric_attenuation_enabled": True,
            "atmospheric_attenuation_replace_exposures": replace_value,
            "rigs": [
                {
                    "rig_id": rig_id,
                    "photo": {
                        "anti_trailing_enabled": False,
                        "motion_tolerance_px": 1.0,
                        "mechanical_vibration_enabled": False,
                        "mechanical_vibration_delay_s": 2,
                        "iso_compensation_enabled": False,
                        "iso_max": 6400,
                    },
                }
                for rig_id in range(1, 5)
            ],
        },
    }


def test_save_exposure_opt_rejects_non_boolean_replace_flag(tmp_path, monkeypatch):
    import flask_app.app as app_module

    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)

    client = app.test_client()
    response = client.post(
        "/api/configs/save_exposure_opt",
        json=_payload("false"),
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body == {
        "error": (
            "atmospheric_attenuation_replace_exposures "
            "must be a boolean"
        )
    }


def test_save_exposure_opt_accepts_boolean_replace_flag(tmp_path, monkeypatch):
    import flask_app.app as app_module

    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)

    client = app.test_client()
    response = client.post(
        "/api/configs/save_exposure_opt",
        json=_payload(False),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"

    saved = tmp_path / "exposure_opt" / "expo_replace_validation_test.json"
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["atmospheric_attenuation_replace_exposures"] is False
