import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ONSTEP_PATHS = (
    ROOT / "plugins" / "mount" / "onstep.py",
    ROOT / "plugins" / "mount" / "onstep_plugin.py",
)
BACKEND_PATH = ROOT / "flask_app" / "app.py"


def test_onstep_preserves_find_home_without_adding_set_home():
    onstep_source = "\n".join(
        path.read_text(encoding="utf-8") for path in ONSTEP_PATHS
    )
    backend_source = BACKEND_PATH.read_text(encoding="utf-8")
    checked_source = onstep_source + "\n" + backend_source

    assert ":hC#" in onstep_source
    assert ":hF#" not in onstep_source
    assert not re.search(r"(?im)^\s*(?:async\s+)?def\s+set_home\s*\(", checked_source)
    assert not re.search(
        r"(?i)@(?:\w+\.)?route\s*\(\s*['\"][^'\"]*set[-_ ]home[^'\"]*['\"]",
        checked_source,
    )
