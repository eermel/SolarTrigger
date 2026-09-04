from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
DRYRUN_FIXTURES = ROOT / "tests" / "fixtures" / "dryrun"


def test_product_configs_contain_no_dryrun_fixtures():
    assert not list(CONFIGS.rglob("*dryrun*"))


def test_dryrun_files_live_only_in_test_fixtures():
    expected = (
        "capture/dryrun_short.json",
        "circumstances/dryrun_short.json",
        "exposure_opt/expo_exposure_dryrun_short.json",
        "photo_cfg/photo_dryrun_short.json",
        "sequence/sequence_dryrun_short.json",
    )

    for relative in expected:
        assert (DRYRUN_FIXTURES / relative).is_file()


def test_product_config_tree_contains_only_product_resources():
    expected_files = {
        "camera_timing/nikon_d850.json",
        "camera_timing/sony_ilce_7m5.json",
        "capture/default.json",
        "gps_default.json",
        "photo_cfg/photo_default.json",
    }

    actual_files = {
        str(path.relative_to(CONFIGS))
        for path in CONFIGS.rglob("*")
        if path.is_file()
    }

    assert expected_files <= actual_files
