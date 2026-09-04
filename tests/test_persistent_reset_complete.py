from backend.persistent_reset import reset_application_var


def test_reset_removes_everything_under_var(tmp_path):
    var_dir = tmp_path / "var"

    files = (
        var_dir / "state" / "state.json",
        var_dir / "logs" / "logs_buffer.jsonl",
        var_dir / "logs" / "rig_traces.jsonl",
        var_dir / "generated" / "rig" / "default.json",
        var_dir / "generated" / "camera_cfg" / "camera.json",
        var_dir / "generated" / "circumstances" / "eclipse.json",
        var_dir / "generated" / "photo_cfg" / "photo.json",
        var_dir / "generated" / "exposure_opt" / "expo.json",
        var_dir / "generated" / "sequence" / "sequence.json",
        var_dir / "generated" / "execution_plan" / "rig1.plan",
        var_dir / "unexpected" / "old-file.bin",
    )

    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("old", encoding="utf-8")

    reset_application_var(var_dir)

    assert var_dir.is_dir()
    assert not any(path.is_file() for path in var_dir.rglob("*"))

    expected_dirs = (
        "state",
        "generated",
        "generated/rig",
        "generated/camera_cfg",
        "generated/circumstances",
        "generated/photo_cfg",
        "generated/exposure_opt",
        "generated/sequence",
        "generated/execution_plan",
        "logs",
    )

    for relative in expected_dirs:
        assert (var_dir / relative).is_dir()

    assert not (var_dir / "unexpected").exists()


def test_reset_works_when_var_does_not_exist(tmp_path):
    var_dir = tmp_path / "var"

    assert not var_dir.exists()

    reset_application_var(var_dir)

    assert var_dir.is_dir()
    assert (var_dir / "state").is_dir()
    assert (var_dir / "generated").is_dir()
    assert (var_dir / "logs").is_dir()


def test_reset_never_touches_product_configs_or_data(tmp_path):
    configs = tmp_path / "configs"
    data = tmp_path / "data"
    var_dir = tmp_path / "var"

    product_config = configs / "capture" / "default.json"
    eclipse = data / "eclipses" / "2027-08-02.json"

    product_config.parent.mkdir(parents=True)
    eclipse.parent.mkdir(parents=True)

    product_config.write_text("product", encoding="utf-8")
    eclipse.write_text("eclipse", encoding="utf-8")

    (var_dir / "generated").mkdir(parents=True)
    (var_dir / "generated" / "delete-me.json").write_text(
        "runtime",
        encoding="utf-8",
    )

    reset_application_var(var_dir)

    assert product_config.read_text(encoding="utf-8") == "product"
    assert eclipse.read_text(encoding="utf-8") == "eclipse"
    assert not (var_dir / "generated" / "delete-me.json").exists()
