from tests.frontend_source import frontend_source
import re
from pathlib import Path


INDEX_HTML = frontend_source()


def test_photo_setup_contains_no_rig_columns():
    start = INDEX_HTML.index('<div class="page" id="page-2">')
    end = INDEX_HTML.index('<!-- /page-2 CFG PHOTO -->', start)
    page = INDEX_HTML[start:end]

    assert "camcfg-rig-column-" not in page
    assert "camera-rigs-section" not in page


def test_exposure_opt_contains_exactly_four_rig_columns():
    start = INDEX_HTML.index('<div class="page" id="page-exposure-opt">')
    end = INDEX_HTML.index('<!-- /Exposure Optimization -->', start)
    page = INDEX_HTML[start:end]

    columns = re.findall(
        r'id="camcfg-rig-column-([1-4])"',
        page,
    )
    assert columns == ["1", "2", "3", "4"]


def test_update_rigs_toggles_exposure_opt_column_visibility():
    update_rigs = re.search(
        r"function updateRigs\(rigs\)\s*\{(?P<body>.*?)\n\}",
        INDEX_HTML,
        re.DOTALL,
    )
    assert update_rigs

    body = update_rigs.group("body")

    assert re.search(
        r"document\.getElementById\("
        r"`camcfg-rig-column-\$\{defaultRig\.rig_id\}`\)",
        body,
    )
    assert (
        "const triggerEnabled = "
        "defaultRig.rig_id === 1 || rig.enabled === true"
        in body
    )
    assert (
        "cameraColumn.classList.toggle('enabled', triggerEnabled)"
        in body
    )
    assert "cameraColumn.hidden = false" in body
