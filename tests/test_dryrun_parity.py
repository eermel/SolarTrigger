from pathlib import Path


ROOT = Path(__file__).parents[1]
SRC = (ROOT / "scripts" / "eclipse_trigger.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _indented_block(source: str, marker: str) -> str:
    """Return the source region controlled by the line containing marker."""
    lines = source.splitlines()
    start = next(index for index, line in enumerate(lines) if marker in line)
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        end += 1
    return "\n".join(lines[start:end])


def test_service_mapping_keeps_dry_run_on_real_camera_service():
    camera_selection = SRC[SRC.index('ipc_socket = os.environ.get("SET_CAMERA_IPC_SOCKET")') :]
    simulation_branch = _indented_block(camera_selection, "if _sim_mode:")
    assert "camera_service = _SimulationCameraService()" in simulation_branch

    ipc_branch = _indented_block(camera_selection, "elif ipc_socket:")
    assert "camera_service = CameraService(" not in ipc_branch

    legacy_branch = camera_selection[camera_selection.index("        else:") :]
    assert "if args.dry_run:" in legacy_branch
    assert "camera_service = CameraService(" in legacy_branch


def test_dry_run_startup_log_describes_timeline_translation():
    log_line = next(
        line for line in SRC.splitlines() if "_log(" in line and "DRY-RUN ×1" in line
    )

    assert "timeline translatée" in log_line
    assert "appareil simulé" not in log_line
    assert "accès matériel caméra totalement désactivé" not in log_line


def test_dry_run_cli_help_describes_timeline_translation():
    help_line = next(
        line
        for line in SRC.splitlines()
        if 'add_argument("--dry-run"' in line
    )

    assert "timeline translatée" in help_line
    assert "sans appareil" not in help_line


def test_rebase_timeline_calls_are_guarded_by_dry_run():
    guarded_region = _indented_block(SRC, "if args.dry_run:")
    occurrences = [
        index
        for index in range(len(SRC))
        if SRC.startswith("rebase_timeline(", index)
    ]

    assert occurrences
    assert guarded_region.count("rebase_timeline(") == len(occurrences)


def test_readme_quick_help_describes_dry_run_parity():
    quick_help_line = next(
        line for line in README.splitlines() if line.lstrip().startswith("#   --dry-run")
    )
    normalized = quick_help_line.casefold()

    assert "--dry-run (simule" not in normalized
    assert "sans appareil" not in normalized
    assert "chronologie" in normalized or "timeline" in normalized or "parité matérielle" in normalized
