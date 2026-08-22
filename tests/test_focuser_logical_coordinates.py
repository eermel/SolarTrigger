import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = ROOT / "services" / "focuser_service.py"
APP_PATH = ROOT / "flask_app" / "app.py"
PLUGIN_DIR = ROOT / "plugins" / "focuser"


def _focuser_sources():
    app_source = APP_PATH.read_text(encoding="utf-8")
    routes_start = app_source.index("# API — FOCUSER")
    routes_end = app_source.index("def _get_camera_model_info", routes_start)

    yield SERVICE_PATH, SERVICE_PATH.read_text(encoding="utf-8")
    yield APP_PATH, app_source[routes_start:routes_end]
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        yield path, path.read_text(encoding="utf-8")


def test_focuser_paths_contain_no_fixed_logical_offset():
    forbidden_patterns = {
        "legacy fixed offset 91": re.compile(r"(?<!\d)91(?!\d)"),
        "numeric offset assignment": re.compile(
            r"(?im)\boffset\b\s*(?::|=)\s*[+-]?\d+"
        ),
        "number documented as a focuser logical/home offset": re.compile(
            r"(?im)^(?=[^\n]*(?:focuser|logical|home))"
            r"[^\n]*\boffset\b[^\n]*\b\d+\b"
        ),
    }

    for path, source in _focuser_sources():
        for description, pattern in forbidden_patterns.items():
            assert not pattern.search(source), f"{description} found in {path}"


def test_home_delegates_to_move_to_logical_zero():
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    service_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "FocuserService"
    )
    home = next(
        node
        for node in service_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "home"
    )

    calls = [node for node in ast.walk(home) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "self"
        and call.func.attr == "move_to"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == 0
        for call in calls
    )
