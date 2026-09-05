from tests.frontend_source import frontend_source
import re
from pathlib import Path


INDEX_HTML = frontend_source()


def _update_rigs_body():
    update_rigs = re.search(
        r"function updateRigs\(rigs\)\s*\{(?P<body>.*?)\n\}"
        r"\s*\n\s*document\.addEventListener",
        INDEX_HTML,
        flags=re.DOTALL,
    )
    assert update_rigs, "updateRigs function is missing"
    return update_rigs.group("body")


def test_update_rigs_hides_inactive_camera_rig_columns():
    body = _update_rigs_body()

    assert re.search(
        r"document\.getElementById\("
        r"`cam-rig-column-\$\{defaultRig\.rig_id\}`\)",
        body,
    )
    assert (
        "const triggerEnabled = "
        "defaultRig.rig_id === 1 || rig.enabled === true"
        in body
    )
    assert re.search(
        r"cameraRigColumn\.classList\.toggle\("
        r"'enabled',\s*triggerEnabled\)",
        body,
    )
    assert re.search(
        r"cameraRigColumn\.hidden\s*=\s*!triggerEnabled",
        body,
    )

def test_update_rigs_does_not_add_camera_polling():
    body = _update_rigs_body()

    assert not re.search(r"\bset(?:Interval|Timeout)\s*\(", body)
