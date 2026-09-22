#!/usr/bin/env python3
"""One-shot, idempotent removal of the obsolete execution-plan architecture."""

from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 0:
        if new and new in text:
            return text
        raise RuntimeError(f"{label}: expected source fragment not found")
    if count != 1:
        raise RuntimeError(f"{label}: expected one source fragment, found {count}")
    return text.replace(old, new, 1)


def remove_ranges(text: str, ranges: list[tuple[int, int]]) -> str:
    lines = text.splitlines(keepends=True)
    remove = set()
    for start, end in ranges:
        remove.update(range(start, end + 1))
    return "".join(line for number, line in enumerate(lines, 1) if number not in remove)


def remove_python_functions_containing(path: str, tokens: tuple[str, ...]) -> None:
    text = read(path)
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    ranges = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        segment = "".join(lines[node.lineno - 1 : node.end_lineno])
        if any(token in segment for token in tokens):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            ranges.append((start, node.end_lineno or node.lineno))
    if ranges:
        write(path, remove_ranges(text, ranges))


def remove_legacy_flask_routes_and_imports() -> None:
    path = "flask_app/app.py"
    text = read(path)
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    ranges: list[tuple[int, int]] = []
    legacy_modules = {
        "backend.sequencer_plan_service",
        "backend.execution_plan_runtime",
        "backend.execution_plan_text",
    }
    legacy_calls = (
        "compile_execution_plan_from_files",
        "compile_rig_execution_plan_from_files",
        "render_execution_plan_text",
        "build_execution_plan_filename",
        "load_execution_plan",
    )
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in legacy_modules:
            ranges.append((node.lineno, node.end_lineno or node.lineno))
            continue
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        segment = "".join(lines[node.lineno - 1 : node.end_lineno])
        remove = (
            node.name.startswith("api_sequencer_")
            or node.name.startswith("api_configs_execution_plan_")
            or node.name == "api_trigger_select_execution_plan"
            or any(token in segment for token in legacy_calls)
        )
        for decorator in node.decorator_list:
            for child in ast.walk(decorator):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    if "/api/sequencer/" in child.value or "execution_plan" in child.value:
                        remove = True
        if remove:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            ranges.append((start, node.end_lineno or node.lineno))
    if ranges:
        write(path, remove_ranges(text, ranges))


def move_camera_timing_profile() -> None:
    path = "backend/camera_timing.py"
    text = read(path)
    text = text.replace("import json\n", "import json\nfrom dataclasses import dataclass\n")
    text = text.replace("from backend.sequencer_compiler import CameraTimingProfile\n", "")
    marker = "\n\n_TIMING_FIELDS = ("
    dataclass_text = '''\n\n@dataclass(frozen=True)\nclass CameraTimingProfile:\n    """Measured/guarded camera timing values used by persistent timing loaders."""\n\n    backend: str\n    set_iso_ms: float = 0.0\n    set_capturemode_ms: float = 0.0\n    set_shutter_ms: float = 0.0\n    trigger_single_lead_ms: float = 0.0\n    trigger_single_duration_ms: float = 0.0\n    bracket_press_lead_ms: float = 0.0\n    bracket_release_ms: float = 0.0\n    settle_idle_ms: float = 0.0\n    bracket_atomic_ms_by_frames: dict[int, float] | None = None\n    session_first_photo_overhead_ms: float = 0.0\n'''
    if "class CameraTimingProfile:" not in text:
        text = replace_once(text, marker, dataclass_text + marker, "camera timing dataclass")
    write(path, text)


def refactor_camera_validation() -> None:
    path = "backend/camera_validation.py"
    text = read(path)
    text = text.replace(
        "The validation deliberately exercises the real execution-plan runtime, local\n"
        "camera IPC, CameraWorker, CameraService and ProfilePlugin.  It does not use EXIF\n",
        "The validation deliberately exercises local Camera IPC, CameraWorker, CameraService\n"
        "and ProfilePlugin through a direct in-memory diagnostic scheduler. It does not use EXIF\n",
    )
    text = text.replace("from datetime import datetime, timedelta, timezone", "from datetime import datetime, timezone")
    text = text.replace("from backend.execution_plan_runtime import ExecutionPlanRuntime, load_execution_plan\n", "")
    text = text.replace("from backend.trigger_runtime import RuntimeClock\n", "")
    scheduler_import = (
        "from backend.camera_validation_scheduler import (\n"
        "    CameraValidationScheduleCancelled,\n"
        "    run_validation_recipe,\n"
        ")\n"
    )
    anchor = "from backend.camera_worker_runtime import CameraWorkerRuntime, get_camera_worker_runtime\n"
    if scheduler_import not in text:
        text = replace_once(text, anchor, anchor + scheduler_import, "camera validation scheduler import")
    text = text.replace(
        '"""Build a short deterministic relative execution plan recipe.\n',
        '"""Build a short deterministic relative camera-validation recipe.\n',
    )
    text, count = re.subn(
        r"\n\ndef materialize_validation_plan\(.*?\n\ndef _same_physical_camera",
        "\n\ndef _same_physical_camera",
        text,
        count=1,
        flags=re.S,
    )
    if count == 0 and "def materialize_validation_plan(" in text:
        raise RuntimeError("materialize_validation_plan removal failed")
    text = text.replace(
        '"measurement": "software_dispatch_start_vs_plan_target"',
        '"measurement": "software_dispatch_start_vs_validation_target"',
    )
    text = re.sub(
        r"\n    skip_lines = \[.*?\n    if fatal_error is not None:",
        "\n    if fatal_error is not None:",
        text,
        count=1,
        flags=re.S,
    )
    text = text.replace("        plan_path: Path | None = None\n", "")
    old_run = '''            first_command = _utc_now() + timedelta(seconds=float(recipe["preflight_reserve_s"]))\n            _plan_document, plan_text = materialize_validation_plan(\n                recipe,\n                rig_id=rig_id,\n                first_command_utc=first_command,\n                profile_filename=prepared["profile_path"].name,\n                timing_filename=prepared["timing_path"].name,\n            )\n            plan_path = run_dir / "validation.plan"\n            plan_path.write_text(plan_text, encoding="utf-8")\n\n            # Re-parse the exact text file that will be executed.  This is part\n            # of the validation: no special in-memory plan bypass exists.\n            plan = load_execution_plan(plan_path)\n            execution = ExecutionPlanRuntime(\n                clock=RuntimeClock(),\n                camera_client=recorder,\n                log_fn=self.log,\n                stop_event=self.cancel_event,\n            )\n            execution.prepare_for_execution(plan)\n            if self.cancel_event.is_set():\n                raise CameraValidationCancelled("validation cancelled after preflight")\n\n            self.phase = "running"\n            execution.run(plan)\n'''
    new_run = '''            self.phase = "running"\n            run_validation_recipe(\n                recipe,\n                rig_id=rig_id,\n                camera_client=recorder,\n                log_fn=self.log,\n                stop_event=self.cancel_event,\n            )\n'''
    text = replace_once(text, old_run, new_run, "camera validation runtime replacement")
    text = text.replace(
        "        except CameraValidationCancelled as exc:\n",
        "        except (CameraValidationCancelled, CameraValidationScheduleCancelled) as exc:\n",
    )
    text = re.sub(
        r'\n\s*"plan_path": _safe_relative\(plan_path, root\) if plan_path else None,',
        "",
        text,
        count=1,
    )
    text = text.replace('    "materialize_validation_plan",\n', '    "run_validation_recipe",\n')
    for token in ("ExecutionPlanRuntime", "load_execution_plan", "materialize_validation_plan", "validation.plan"):
        if token in text:
            raise RuntimeError(f"camera_validation.py still contains {token}")
    write(path, text)


def refactor_camera_validation_tests() -> None:
    path = "tests/test_camera_validation.py"
    text = read(path)
    text = text.replace("from datetime import datetime, timezone\n", "")
    text = text.replace("    materialize_validation_plan,\n", "")
    text = text.replace("from backend.execution_plan_runtime import load_execution_plan\n", "")
    write(path, text)
    remove_python_functions_containing(
        path,
        ("materialize_validation_plan", "load_execution_plan", "validation.plan"),
    )
    text = read(path).replace(
        "(see EXECUTION_PLAN skip_past in the field log).",
        "(the direct validation scheduler must reserve that cold-start cost).",
    )
    write(path, text)


def refactor_mixed_tests() -> None:
    # Keep current profile/characterization coverage; remove only tests whose
    # subject is the retired Sequencer/compiler/runtime architecture.
    remove_python_functions_containing(
        "tests/test_camera_profile_only_architecture.py",
        ("backend.sequencer_compiler", "audit_materialized_capture"),
    )
    remove_python_functions_containing(
        "tests/test_new_characterization_completion.py",
        ("backend.sequencer_compiler", "audit_materialized_capture"),
    )
    remove_python_functions_containing(
        "tests/test_camera_profiles.py",
        ("backend.sequencer_compiler", "audit_materialized_capture", "schedule_audited_capture"),
    )
    remove_python_functions_containing(
        "tests/test_camera_timing_contract.py",
        ("ExecutionPlanRuntime",),
    )
    text = read("tests/test_camera_timing_contract.py")
    text = text.replace("from backend.execution_plan_runtime import ExecutionPlanRuntime\n", "")
    write("tests/test_camera_timing_contract.py", text)

    remove_python_functions_containing(
        "tests/test_trigger_runtime_resilience.py",
        (
            "ExecutionPlanRuntime",
            "CaptureTarget(",
            "AuditedRigCapture(",
            "reduce_audited_capture_operations",
        ),
    )
    text = read("tests/test_trigger_runtime_resilience.py")
    text = text.replace("from datetime import datetime, timedelta\n", "")
    text = re.sub(
        r"from backend\.execution_plan_runtime import ExecutionPlanRuntime\n",
        "",
        text,
    )
    text = re.sub(
        r"from backend\.sequencer_compiler import \(.*?\)\n",
        "",
        text,
        count=1,
        flags=re.S,
    )
    # Clock/_command become unused once the ExecutionPlanRuntime tests are gone.
    text = re.sub(r"\n\nclass Clock:.*?\n\ndef test_camera_ipc_allows_disjoint_rig_sessions", "\n\ndef test_camera_ipc_allows_disjoint_rig_sessions", text, count=1, flags=re.S)
    write("tests/test_trigger_runtime_resilience.py", text)


def refactor_frontend() -> None:
    js_path = "flask_app/static/js/solartrigger.js"
    js = read(js_path)
    start_marker = (
        "// ════════════════════════════════════════════════════════════════\n"
        "// SEQUENCER\n"
        "// ════════════════════════════════════════════════════════════════\n"
    )
    camera_marker = (
        "// ════════════════════════════════════════════════════════════════\n"
        "// CAMERA VALIDATION — end-to-end real execution-plan run\n"
        "// ════════════════════════════════════════════════════════════════\n"
    )
    if start_marker in js and camera_marker in js:
        before, remainder = js.split(start_marker, 1)
        _legacy, after = remainder.split(camera_marker, 1)
        init = "loadSupportedEclipses();\nloadEclipseData();\nloadCameraStatus();\n\n"
        js = before + init + (
            "// ════════════════════════════════════════════════════════════════\n"
            "// CAMERA VALIDATION — end-to-end real camera run\n"
            "// ════════════════════════════════════════════════════════════════\n"
        ) + after
    js = js.replace("  else if (source === 'sequencer') containerId = 'log-container-sequencer';\n", "")
    js = js.replace(
        "    const maxLines =\n      source === 'sequencer'\n        ? 3000\n        : 600;\n",
        "    const maxLines = 600;\n",
    )
    write(js_path, js)

    html_path = "flask_app/templates/index.html"
    html = read(html_path)
    html = re.sub(
        r'\n\s*<button class="tab" data-page-index="5" id="sequencer-tab".*?</button>',
        "",
        html,
        count=1,
        flags=re.S,
    )
    html, count = re.subn(
        r'\n\s*<!-- ═+ SEQUENCER ═+ -->\s*<div class="page" id="sequencer-panel" hidden>.*?</div><!-- /sequencer-panel -->',
        '\n    <div class="page" id="retired-page-5" hidden></div>',
        html,
        count=1,
        flags=re.S,
    )
    if count == 0 and 'id="retired-page-5"' not in html:
        raise RuntimeError("sequencer panel not found")
    write(html_path, html)


def rewrite_resilience_doc() -> None:
    content = (
        "# Trigger runtime resilience contract\n\n"
        "## START / preflight\n\n"
        "Before a RIG enters timed capture, the characterized camera is connected and checked. "
        "Characterized commands carry independent `get` and `set` capabilities. The camera profile "
        "performs authoritative readback and sends SET only when a value must change.\n\n"
        "## Live phase execution\n\n"
        "The live eclipse trigger is driven by `scripts/eclipse_trigger.py` and "
        "`backend.phase_trigger.PhaseRuntime`. The current eclipse phase and configured photographic "
        "policy are the runtime authority. No intermediate execution-plan file is generated or "
        "consumed by the live trigger. PHOTO is never blindly replayed after an ambiguous transport "
        "failure because the shutter may already have fired.\n\n"
        "## USB failure / battery replacement\n\n"
        "The camera worker invalidates a stale USB handle on transport failure. A later operation can "
        "reconnect to the configured physical camera identity. Camera photographic settings are "
        "reconciled through the characterized profile before capture when required.\n\n"
        "## Camera Validation\n\n"
        "Camera Validation uses a relative in-memory diagnostic recipe. It preflights the real camera "
        "endpoint first, then dispatches SET/PHOTO operations through Camera IPC and the real camera "
        "worker from a monotonic anchor. Validation artifacts are the run log and JSON report; there "
        "is no intermediate scheduler file.\n"
    )
    (ROOT / "docs/TRIGGER_RUNTIME_RESILIENCE.md").write_text(content, encoding="utf-8")


def delete_legacy_files() -> None:
    paths = [
        "backend/anchor_sequencer.py",
        "backend/execution_plan_runtime.py",
        "backend/execution_plan_text.py",
        "backend/sequencer_compiler.py",
        "backend/sequencer_plan_service.py",
        "tests/test_anchor_sequencer.py",
        "tests/test_atmos_partial_only_20260916.py",
        "tests/test_camera_mechanical_vibration_scheduling.py",
        "tests/test_execution_plan_hotplug_resilience.py",
        "tests/test_execution_plan_runtime.py",
        "tests/test_execution_plan_text.py",
        "tests/test_frontend_sequencer_per_rig_plan.py",
        "tests/test_nikon_sequencer_audit.py",
        "tests/test_sequencer_atmos_full_bracket_20260916.py",
        "tests/test_sequencer_compile_route.py",
        "tests/test_sequencer_compiler.py",
        "tests/test_sequencer_per_rig_api.py",
        "tests/test_sequencer_plan_service.py",
        "tests/test_sequencer_single_rig_plan.py",
        "tests/test_sony_sequencer_audit.py",
        "ARCHITECTURE_V6.md",
        "TIME_AUDIT_V7.md",
        "TIME_MODEL_V71.md",
    ]
    for relative in paths:
        path = ROOT / relative
        if path.exists():
            path.unlink()
    old = ROOT / "tests/test_camera_ipc_execution_plan_ops.py"
    new = ROOT / "tests/test_camera_ipc_scheduled_ops.py"
    if old.exists() and not new.exists():
        old.rename(new)


def assert_no_runtime_legacy() -> None:
    forbidden = (
        "backend.execution_plan_runtime",
        "backend.execution_plan_text",
        "backend.sequencer_plan_service",
        "backend.sequencer_compiler",
        "backend.anchor_sequencer",
        "ExecutionPlanRuntime",
        "load_execution_plan",
        "materialize_validation_plan",
        "validation.plan",
        "compile_execution_plan_from_files",
        "compile_rig_execution_plan_from_files",
        "render_execution_plan_text",
        "build_execution_plan_filename",
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
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)}: {token}")
    if offenders:
        raise RuntimeError("legacy execution-plan references remain:\n" + "\n".join(offenders))


def main() -> None:
    remove_legacy_flask_routes_and_imports()
    move_camera_timing_profile()
    refactor_camera_validation()
    refactor_camera_validation_tests()
    refactor_mixed_tests()
    refactor_frontend()
    rewrite_resilience_doc()
    delete_legacy_files()
    assert_no_runtime_legacy()
    print("execution-plan legacy refactor applied successfully")


if __name__ == "__main__":
    main()
