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
    legacy_marker = "# ── Connexion caméra via CameraService / CameraPlugin"
    camera_selection = SRC[SRC.index(legacy_marker):]
    simulation_branch = _indented_block(camera_selection, "if _sim_mode:")
    assert "camera_service = _SimulationCameraService()" in simulation_branch

    ipc_branch = _indented_block(camera_selection, "elif ipc_socket:")
    assert "camera_service = CameraService(" not in ipc_branch

    legacy_branch = camera_selection[camera_selection.index("        else:") :]
    assert "if args.dry_run:" in legacy_branch
    assert "camera_service = CameraService(" in legacy_branch


def test_dry_run_startup_log_describes_timeline_translation():
    marker = "🧪 DRY-RUN ×1"
    start = SRC.index(marker)

    log_block_start = SRC.rfind("_log(", 0, start)
    log_block_end = SRC.index(")", start) + 1
    log_block = SRC[log_block_start:log_block_end]

    assert "timeline translatée" in log_block
    assert "appareil simulé" not in log_block
    assert "accès matériel caméra totalement désactivé" not in log_block


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


def test_execution_plan_dry_run_uses_one_uniform_plan_rebase():
    import ast

    tree = ast.parse(SRC)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_execution_plan_v2"
    )

    dry_run_branch = next(
        node
        for node in function.body
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "args.dry_run"
    )

    guarded_calls = [
        node
        for node in ast.walk(dry_run_branch)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "rebase_execution_plan"
    ]

    # Le plan principal ne doit être rebasé qu'une seule fois pour le
    # dry-run. D'autres rebases peuvent exister dans la fonction, notamment
    # pour chaque cycle du TOTALITY OVERRIDE.
    assert len(guarded_calls) == 1
