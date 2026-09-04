from backend.rig_config import canonical_rig_defaults, save
from backend.rig_runtime import (
    _resolve_generated_dir,
    _resolve_rig_config_file,
)


def _rig_config():
    return {
        "schema_version": 2,
        "eclipse": None,
        "sequence": {"common": {}},
        "rigs": [canonical_rig_defaults(1)],
    }


def test_rig_paths_are_under_project_var(tmp_path):
    assert _resolve_generated_dir(tmp_path) == (
        tmp_path / "var" / "generated"
    )
    assert _resolve_rig_config_file(tmp_path) == (
        tmp_path
        / "var"
        / "generated"
        / "rig"
        / "default.json"
    )


def test_rig_config_save_recreates_missing_parent(tmp_path):
    destination = (
        tmp_path
        / "var"
        / "generated"
        / "rig"
        / "default.json"
    )

    assert not destination.parent.exists()

    save(destination, _rig_config())

    assert destination.is_file()
