import re
from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def _update_rigs_body():
    update_rigs = re.search(
        r"function updateRigs\(rigs\)\s*\{(?P<body>.*?)\n\}"
        r"\s*\n\s*document\.addEventListener",
        INDEX_HTML,
        flags=re.DOTALL,
    )
    assert update_rigs, "updateRigs function is missing"
    return update_rigs.group("body")


def test_update_rigs_toggles_camera_rig_columns():
    body = _update_rigs_body()

    assert re.search(
        r"document\.getElementById\(`cam-rig-column-\$\{defaultRig\.rig_id\}`\)",
        body,
    )
    assert re.search(
        r"cameraRigColumn\.classList\.toggle\('enabled',\s*enabled\)", body
    )
    assert re.search(r"cameraRigColumn\.hidden\s*=\s*!enabled", body)


def test_update_rigs_does_not_add_camera_polling():
    body = _update_rigs_body()

    assert not re.search(r"\bset(?:Interval|Timeout)\s*\(", body)
