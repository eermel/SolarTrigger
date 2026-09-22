#!/usr/bin/env python3
"""Apply the precise frontend/test part of the execution-plan retirement.

The primary refactor intentionally runs first.  This companion pass restores
frontend source files from the branch commit, then removes only the retired
Sequencer section.  This prevents shared navigation/startup code from being
mistaken for Sequencer code.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def git_head_text(path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"HEAD:{path}"], cwd=ROOT, text=True
    )


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new and new in text:
            return text
        raise RuntimeError(f"{label}: source fragment not found")
    return text.replace(old, new, 1)


def remove_test_functions(path: str, tokens: tuple[str, ...]) -> None:
    text = read(path)
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    remove: set[int] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        segment = "".join(lines[node.lineno - 1 : node.end_lineno])
        if any(token in segment for token in tokens):
            start = node.lineno
            while start > 1 and not lines[start - 2].strip():
                start -= 1
            remove.update(range(start, (node.end_lineno or node.lineno) + 1))
    if remove:
        write(path, "".join(line for n, line in enumerate(lines, 1) if n not in remove))


def refactor_js() -> None:
    path = "flask_app/static/js/solartrigger.js"
    js = git_head_text(path)
    start_marker = (
        "// ════════════════════════════════════════════════════════════════\n"
        "// SEQUENCER\n"
        "// ════════════════════════════════════════════════════════════════\n"
    )
    next_marker = (
        "// ════════════════════════════════════════════════════════════════\n"
        "// NAVIGATION\n"
        "// ════════════════════════════════════════════════════════════════\n"
    )
    if start_marker not in js or next_marker not in js:
        raise RuntimeError("cannot isolate Sequencer JS section")
    before, remainder = js.split(start_marker, 1)
    _legacy, after = remainder.split(next_marker, 1)
    js = before + next_marker + after

    js = js.replace("'sequencer-panel'", "'retired-page-5'", 1)
    js = js.replace(
        "  else if (source === 'sequencer') containerId = 'log-container-sequencer';\n",
        "",
    )
    js = js.replace(
        "    const maxLines =\n      source === 'sequencer'\n        ? 3000\n        : 600;\n",
        "    const maxLines = 600;\n",
    )
    js = js.replace(
        "// CAMERA VALIDATION — end-to-end real execution-plan run",
        "// CAMERA VALIDATION — end-to-end real camera run",
    )
    if "/api/sequencer/" in js or "validation.plan" in js or "execution-plan run" in js:
        raise RuntimeError("Sequencer/execution-plan JS reference remains")
    write(path, js)


def refactor_html() -> None:
    path = "flask_app/templates/index.html"
    html = git_head_text(path)
    html, tab_count = re.subn(
        r'\n\s*<button class="tab" data-page-index="5" id="sequencer-tab".*?</button>',
        "",
        html,
        count=1,
        flags=re.S,
    )
    html, panel_count = re.subn(
        r'\n\s*<!-- ═+ SEQUENCER ═+ -->\s*'
        r'<div class="page" id="sequencer-panel" hidden>.*?'
        r'</div><!-- /sequencer-panel -->',
        '\n    <div class="page" id="retired-page-5" hidden></div>',
        html,
        count=1,
        flags=re.S,
    )
    if tab_count != 1 or panel_count != 1:
        raise RuntimeError(
            f"expected one Sequencer tab/panel, got tab={tab_count} panel={panel_count}"
        )
    if ".plan" in html or "sequencer-panel" in html or "sequencer-tab" in html:
        raise RuntimeError("retired Sequencer HTML remains")
    write(path, html)


def adapt_frontend_tests() -> None:
    # Tests whose entire subject is the retired Sequencer UI are obsolete.
    remove_test_functions(
        "tests/test_exposure_opt_camera_vibration_ui.py",
        ("log-container-sequencer", "sequencer_log"),
    )
    remove_test_functions(
        "tests/test_frontend_operator_feedback_regressions.py",
        ("sequence generated successfully",),
    )

    path = "tests/test_frontend_operator_feedback_regressions.py"
    text = read(path).replace(
        "// CAMERA VALIDATION — end-to-end real execution-plan run",
        "// CAMERA VALIDATION — end-to-end real camera run",
    )
    write(path, text)

    path = "tests/test_frontend_controls_tab.py"
    text = read(path)
    text = text.replace('        "SEQUENCER",\n', "")
    text = text.replace('        "sequencer-panel",\n', '        "retired-page-5",\n')
    text = text.replace(
        "    controls = parser.tabs[8]\n    trigger = parser.tabs[9]\n    debug = parser.tabs[10]\n",
        "    controls = parser.tabs[7]\n    trigger = parser.tabs[8]\n    debug = parser.tabs[9]\n",
    )
    write(path, text)

    path = "tests/test_frontend_focuser_ui.py"
    text = read(path)
    text = text.replace('        "SEQUENCER",\n', "")
    text = text.replace(
        "    assert targets == [9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 10]\n",
        "    assert targets == [9, 0, 1, 2, 3, 4, 6, 7, 8, 10]\n",
    )
    write(path, text)

    path = "tests/test_frontend_tabs_clickable.py"
    text = read(path)
    text = text.replace('        "SEQUENCER",\n', "")
    text = text.replace(
        "        [f\"showTab({index})\"] for index in [9, *range(9), 10]\n",
        "        [f\"showTab({index})\"] for index in [9, 0, 1, 2, 3, 4, 6, 7, 8, 10]\n",
    )
    write(path, text)

    path = "tests/test_frontend_tabs_click_ui.py"
    text = read(path).replace("    assert len(tabs) == 11\n", "    assert len(tabs) == 10\n")
    write(path, text)

    path = "tests/test_frontend_trigger_ui.py"
    text = read(path)
    old = '''    sequencer_start = html.index('id="sequencer-tab"')\n    sequencer_end = html.index("</button>", sequencer_start)\n    assert "workflow-step-number" not in html[sequencer_start:sequencer_end]\n'''
    text = replace_required(
        text,
        old,
        '''    assert 'id="sequencer-tab"' not in html\n''',
        "trigger UI retired Sequencer assertion",
    )
    write(path, text)

    path = "tests/test_select_chevron_ui.py"
    text = read(path)
    for line in (
        '    "sequencer-circumstances-select",\n',
        '    "sequencer-photo-select",\n',
        '    "sequencer-exposure-opt-select",\n',
    ):
        text = text.replace(line, "")
    write(path, text)


def main() -> None:
    refactor_js()
    refactor_html()
    adapt_frontend_tests()
    print("precise Sequencer frontend retirement applied successfully")


if __name__ == "__main__":
    main()
