from pathlib import Path

from backend.trigger_service import TriggerService


def _service(tmp_path, *, product_configs_dir=None):
    return TriggerService(
        state_store=object(),
        trigger_script=tmp_path / "scripts" / "eclipse_trigger.py",
        json_file=tmp_path / "var" / "generated" / "todayeclipse.json",
        configs_dir=tmp_path / "var" / "generated",
        product_configs_dir=product_configs_dir,
        log_fn=lambda *_args, **_kwargs: None,
        emit_fn=lambda *_args, **_kwargs: None,
    )


def test_generated_camera_config_has_priority(tmp_path):
    generated = (
        tmp_path
        / "var"
        / "generated"
        / "camera_cfg"
        / "camera.json"
    )
    bundled = (
        tmp_path
        / "configs"
        / "capture"
        / "camera.json"
    )

    generated.parent.mkdir(parents=True)
    bundled.parent.mkdir(parents=True)

    generated.write_text("generated", encoding="utf-8")
    bundled.write_text("bundled", encoding="utf-8")

    service = _service(
        tmp_path,
        product_configs_dir=tmp_path / "configs",
    )

    assert service._resolve_camera_config("camera.json") == generated


def test_bundled_capture_is_read_from_product_configs(tmp_path):
    bundled = (
        tmp_path
        / "configs"
        / "capture"
        / "default.json"
    )
    bundled.parent.mkdir(parents=True)
    bundled.write_text("{}", encoding="utf-8")

    service = _service(
        tmp_path,
        product_configs_dir=tmp_path / "configs",
    )

    assert service._resolve_camera_config("default.json") == bundled


def test_missing_camera_config_does_not_create_directories(tmp_path):
    service = _service(
        tmp_path,
        product_configs_dir=tmp_path / "configs",
    )

    assert service._resolve_camera_config("missing.json") is None
    assert not (tmp_path / "var").exists()
    assert not (tmp_path / "configs").exists()


def test_default_keeps_single_root_compatibility(tmp_path):
    capture = (
        tmp_path
        / "var"
        / "generated"
        / "capture"
        / "legacy-test.json"
    )
    capture.parent.mkdir(parents=True)
    capture.write_text("{}", encoding="utf-8")

    service = _service(tmp_path)

    assert service._resolve_camera_config("legacy-test.json") == capture
