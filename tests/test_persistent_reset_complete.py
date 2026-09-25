from backend.persistent_reset import reset_application_var


def test_reset_removes_mutable_data_and_preserves_tls(tmp_path):
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
        var_dir / "generated" / "camera_profiles" / "profile.json",
        var_dir / "generated" / "camera_timing" / "timing.json",
        var_dir / "generated" / "camera_characterization" / "history.jsonl",
        var_dir / "unexpected" / "old-file.bin",
    )
    tls_cert = var_dir / "tls" / "solartrigger-server.crt"
    tls_key = var_dir / "tls" / "solartrigger-server.key"

    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("old", encoding="utf-8")
    tls_cert.parent.mkdir(parents=True, exist_ok=True)
    tls_cert.write_text("certificate", encoding="utf-8")
    tls_key.write_text("private-key", encoding="utf-8")

    reset_application_var(var_dir)

    assert var_dir.is_dir()
    assert tls_cert.read_text(encoding="utf-8") == "certificate"
    assert tls_key.read_text(encoding="utf-8") == "private-key"
    assert all(not path.exists() for path in files)

    expected_dirs = (
        "state",
        "generated",
        "generated/rig",
        "generated/camera_cfg",
        "generated/circumstances",
        "generated/photo_cfg",
        "generated/exposure_opt",
        "generated/sequence",
        "generated/camera_profiles",
        "generated/camera_timing",
        "generated/camera_characterization",
        "generated/camera_characterization/validation",
        "logs",
    )

    for relative in expected_dirs:
        assert (var_dir / relative).is_dir()

    assert not (var_dir / "unexpected").exists()


def test_reset_preserves_deployment_var_symlink_and_resets_shared_target(tmp_path):
    shared_var = tmp_path / "shared-var"
    shared_var.mkdir()
    stale_state = shared_var / "state" / "state.json"
    stale_state.parent.mkdir(parents=True)
    stale_state.write_text("old", encoding="utf-8")
    tls_cert = shared_var / "tls" / "solartrigger-server.crt"
    tls_cert.parent.mkdir(parents=True)
    tls_cert.write_text("certificate", encoding="utf-8")

    dev_active = tmp_path / "dev-active"
    dev_active.mkdir()
    var_link = dev_active / "var"
    var_link.symlink_to(shared_var, target_is_directory=True)

    reset_application_var(var_link)

    assert var_link.is_symlink()
    assert var_link.resolve() == shared_var.resolve()
    assert not stale_state.exists()
    assert tls_cert.read_text(encoding="utf-8") == "certificate"
    assert (shared_var / "state").is_dir()
    assert (shared_var / "generated" / "camera_characterization" / "validation").is_dir()
    assert (shared_var / "logs").is_dir()


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
