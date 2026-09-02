import re
from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1]
    / "flask_app"
    / "templates"
    / "index.html"
).read_text(encoding="utf-8")


def _exposure_page():
    start = INDEX_HTML.index('<div class="page" id="page-exposure-opt">')
    end = INDEX_HTML.index('<!-- /Exposure Optimization -->', start)
    return INDEX_HTML[start:end]


def test_exposure_opt_has_four_rig_columns():
    page = _exposure_page()

    section = re.search(
        r'<section\b[^>]*class="camera-rigs-section"[^>]*>'
        r'(?P<body>.*?)</section>',
        page,
        re.DOTALL,
    )
    assert section, "RIG section is missing from Exposure Optimization"

    columns = re.findall(
        r'<div\b(?=[^>]*class="[^"]*\bcamcfg-rig-column\b[^"]*")'
        r'(?=[^>]*id="camcfg-rig-column-([1-4])")'
        r'(?=[^>]*data-rig-id="([1-4])")[^>]*>',
        section.group("body"),
    )

    assert columns == [
        (str(rig_id), str(rig_id))
        for rig_id in range(1, 5)
    ]


def test_exposure_opt_rig_columns_have_preview_controls_and_targets():
    page = _exposure_page()

    for rig_id in range(1, 5):
        column_start = page.index(f'id="camcfg-rig-column-{rig_id}"')

        if rig_id < 4:
            column_end = page.index(
                f'id="camcfg-rig-column-{rig_id + 1}"',
                column_start,
            )
        else:
            column_end = page.index("</section>", column_start)

        column = page[column_start:column_end]

        assert re.search(
            rf'<button\b'
            rf'(?=[^>]*class="[^"]*\brig-preview-button\b[^"]*")'
            rf'(?=[^>]*data-rig-id="{rig_id}")[^>]*>'
            rf'\s*Preview\s*</button>',
            column,
        )

        assert f'id="rig-preview-{rig_id}"' in column


def test_updateRigs_still_controls_exposure_opt_rig_columns():
    match = re.search(
        r"function updateRigs\(rigs\)\s*\{(?P<body>.*?)\n\}"
        r"\s*\n\s*document\.addEventListener",
        INDEX_HTML,
        re.DOTALL,
    )
    assert match

    body = match.group("body")

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
    assert "rig-preview-button" in body
