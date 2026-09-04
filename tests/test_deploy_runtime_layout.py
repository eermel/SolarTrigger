from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SCRIPT = (
    ROOT
    / "tools"
    / "deploy-prod.sh"
).read_text(encoding="utf-8")

RUNTIME_SCRIPTS = (
    "__init__.py",
    "camera_ipc_client.py",
    "eclipse_calculator_py.py",
    "eclipse_trigger.py",
    "fanout_camera_adapter.py",
    "gps_sync.py",
)


def test_deploy_defines_exact_runtime_scripts():
    for script in RUNTIME_SCRIPTS:
        assert f'"{script}"' in SCRIPT

    assert "measure_camera_wakeup.py" not in SCRIPT
    assert "eclipse_dataset_builder.py" not in SCRIPT
    assert "totality_only.py" not in SCRIPT


def test_deploy_validates_sources_before_any_prod_copy():
    validation = 'if [[ ! -f "$src" ]]; then'
    first_prod_copy = 'echo "=== backend ==="'
    cleanup = (
        '"rm -rf \'$DST/scripts\' && '
        'mkdir -p \'$DST/scripts\'"'
    )
    copy = '"${RUNTIME_SCRIPT_SOURCES[@]}"'

    assert validation in SCRIPT
    assert first_prod_copy in SCRIPT
    assert cleanup in SCRIPT
    assert copy in SCRIPT

    assert SCRIPT.index(validation) < SCRIPT.index(first_prod_copy)
    assert SCRIPT.index(cleanup) < SCRIPT.index(copy)


def test_deploy_does_not_enable_global_rsync_delete():
    start = SCRIPT.index("RSYNC_OPTS=(")
    end = SCRIPT.index(
        '\n)\n\nif [[ "$DRY_RUN"',
        start,
    )

    rsync_options = SCRIPT[start:end]

    assert "--delete" not in rsync_options
