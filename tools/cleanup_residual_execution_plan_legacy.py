#!/usr/bin/env python3
"""Remove the remaining persisted/documentary execution-plan legacy."""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_required(path: str, old: str, new: str, label: str) -> None:
    text = read(path)
    if old not in text:
        if new and new in text:
            return
        raise RuntimeError(f"{label}: source fragment not found in {path}")
    write(path, text.replace(old, new, 1))


def remove_lines_containing(path: str, needles: tuple[str, ...]) -> None:
    text = read(path)
    lines = [
        line for line in text.splitlines(keepends=True)
        if not any(needle in line for needle in needles)
    ]
    write(path, "".join(lines))


def clean_backend() -> None:
    replace_required(
        "backend/camera_characterization.py",
        "    # skip a later command\n    # (see execution_plan budget_overrun_ms handling).\n",
        "    # make a later operation exceed its characterized timing budget.\n",
        "cold-start comment",
    )

    replace_required(
        "scripts/eclipse_trigger.py",
        "The trigger never consumes an Execution Plan and never schedules individual\n"
        "camera SET commands. One camera plugin owns each complete capture operation.\n",
        "The trigger is phase-driven. It schedules phase capture operations directly,\n"
        "while one camera plugin owns each complete camera capture operation.\n",
        "trigger architecture docstring",
    )

    analysis = ROOT / "backend/trigger_run_analysis.py"
    if analysis.exists():
        analysis.unlink()


def clean_docs() -> None:
    path = "docs/CAMERA_CHARACTERIZATION.md"
    text = read(path)
    text = text.replace("- it is absent from the generated `.plan`;\n", "")
    text, count = re.subn(
        r"## Sequencer and `\.plan`\n.*?\n## Physical shutter latency",
        """## Runtime consumption\n\nContract v3 provides guarded timing values to the phase-driven Trigger and to\nCamera Validation. The live Trigger schedules phase capture operations directly;\nCamera Validation uses its own relative in-memory diagnostic recipe. Neither path\nmaterializes an intermediate scheduler file.\n\nEach optimized camera capture remains self-contained (ISO, capture mode if needed,\nshutter, bracket mode if needed, PHOTO). If a USB operation fails, a future\ncomplete capture can reconcile the camera state; past photographs are never\nblindly replayed.\n\nLegacy timing/profile formats remain readable where compatibility is explicitly\nimplemented. A camera must be recharacterized to obtain the current timing\ncontract v3 measurements.\n\n## Physical shutter latency""",
        text,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise RuntimeError("CAMERA_CHARACTERIZATION legacy section not found")
    write(path, text)

    path = "docs/CAMERA_TIMING_CONTRACT.md"
    text = read(path)
    text = text.replace(
        "Le format `.plan` conserve l'enveloppe de garde existante\n"
        "`timing_contract_version=2` pour le transport IPC et le rejet des commandes\n"
        "périmées. Cette valeur est un détail du protocole d'exécution ; les durées qu'elle\n"
        "transporte proviennent du modèle caméra v3.\n",
        "Le Trigger consomme directement les valeurs du contrat caméra v3 pendant\n"
        "l'exécution des phases. Camera Validation utilise les mêmes budgets pour sa\n"
        "recette diagnostique en mémoire et passe par Camera IPC / CameraWorker.\n",
    )
    text = text.replace("- ne sont pas écrites dans le `.plan` ;\n", "")
    write(path, text)


def clean_install() -> None:
    path = "install/install_solareclipse.sh"
    text = read(path)
    text = text.replace('     "$VAR_DIR/generated/execution_plan"', "")
    write(path, text)


def clean_tests() -> None:
    remove_lines_containing(
        "tests/test_persistent_reset_complete.py",
        (
            'var_dir / "generated" / "execution_plan" / "rig1.plan"',
            '"generated/execution_plan",',
        ),
    )

    path = "tests/test_runtime_paths.py"
    text = read(path)
    text = re.sub(
        r"\n    assert runtime_paths\.EXECUTION_PLAN_DIR == \(.*?\n    \)\n",
        "\n",
        text,
        count=1,
        flags=re.S,
    )
    text = text.replace('        "generated/execution_plan",\n', "")
    write(path, text)

    remove_lines_containing(
        "tests/test_var_install_deploy_layout.py",
        ('$VAR_DIR/generated/execution_plan',),
    )

    path = "tests/test_phase_trigger_inputs.py"
    text = read(path)
    text = text.replace(
        "def test_three_selected_files_replace_execution_plan(tmp_path, monkeypatch):",
        "def test_three_selected_files_drive_phase_trigger(tmp_path, monkeypatch):",
    )
    text = text.replace(
        "def test_runtime_command_uses_three_sources_and_no_execution_plan(tmp_path, monkeypatch):",
        "def test_runtime_command_uses_three_selected_sources(tmp_path, monkeypatch):",
    )
    text = text.replace('    assert "--execution-plan" not in seen["command"]\n', "")
    write(path, text)

    remove_lines_containing(
        "tests/test_frontend_operator_feedback_regressions.py",
        (".plan",),
    )
    remove_lines_containing(
        "tests/test_user_facing_english.py",
        ("source .plan inchangé",),
    )

    path = "tests/test_trigger_select_route.py"
    text = read(path)
    text, count = re.subn(
        r"\n    execution_plan_dir = configs_dir / \"execution_plan\".*?\n    state_store\.set\(\"execution_plan_file_rig_1\", execution_plan_name\)\n",
        "\n",
        text,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise RuntimeError("trigger route legacy setup block not found")
    text = text.replace(
        "def test_trigger_start_rejects_missing_execution_plan_circumstances(\n",
        "def test_trigger_start_rejects_missing_selected_circumstances(\n",
    )
    write(path, text)

    path = "tests/test_trigger_service_ipc_lifecycle_endstates.py"
    text = read(path)
    text, count = re.subn(
        r"\n    execution_plan_dir = configs / \"execution_plan\".*?\n    store\.set\(\"execution_plan_file_rig_1\", execution_plan_name\)\n",
        "\n",
        text,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise RuntimeError("trigger service legacy setup block not found")
    write(path, text)

    analysis_test = ROOT / "tests/test_trigger_run_analysis.py"
    if analysis_test.exists():
        analysis_test.unlink()


def assert_core_legacy_removed() -> None:
    tokens = (
        "ExecutionPlanRuntime",
        "execution_plan_runtime",
        "execution_plan_text",
        "sequencer_compiler",
        "sequencer_plan_service",
        "anchor_sequencer",
        "/api/sequencer/",
        "generated/execution_plan",
        "execution_plan_file",
        "config_type\": \"execution_plan",
        "validation.plan",
    )
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path == Path(__file__).resolve():
            continue
        if path.suffix not in {".py", ".js", ".html", ".md", ".json", ".yml", ".yaml", ".sh"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in tokens:
            if token in content:
                offenders.append(f"{path.relative_to(ROOT)}: {token}")
    if offenders:
        raise RuntimeError("residual execution-plan architecture:\n" + "\n".join(offenders))


def main() -> None:
    clean_backend()
    clean_docs()
    clean_install()
    clean_tests()
    assert_core_legacy_removed()
    print("residual execution-plan legacy removed")


if __name__ == "__main__":
    main()
