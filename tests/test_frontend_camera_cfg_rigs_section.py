import re
from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def test_camera_cfg_has_four_rig_columns():
    page_start = INDEX_HTML.index('<div class="page" id="page-2">')
    page_end = INDEX_HTML.index('<!-- /page-2 CFG PHOTO -->', page_start)
    camera_cfg_page = INDEX_HTML[page_start:page_end]

    title_position = camera_cfg_page.index("Camera configuration")
    section = re.search(
        r'<section\b[^>]*class="camera-rigs-section"[^>]*>'
        r'(?P<body>.*?)</section>',
        camera_cfg_page,
        flags=re.DOTALL,
    )
    assert section, "Camera RIGs section is missing from the CFG PHOTO page"
    assert title_position < section.start()

    columns = re.findall(
        r'<div\b(?=[^>]*class="[^"]*\bcamcfg-rig-column\b[^"]*")'
        r'(?=[^>]*id="camcfg-rig-column-([1-4])")'
        r'(?=[^>]*data-rig-id="([1-4])")[^>]*>',
        section.group("body"),
    )
    assert columns == [(str(rig_id), str(rig_id)) for rig_id in range(1, 5)]


def test_camera_cfg_rig_columns_have_preview_controls_and_targets():
    page_start = INDEX_HTML.index('<div class="page" id="page-2">')
    page_end = INDEX_HTML.index('<!-- /page-2 CFG PHOTO -->', page_start)
    camera_cfg_page = INDEX_HTML[page_start:page_end]

    for rig_id in range(1, 5):
        column_start = camera_cfg_page.index(f'id="camcfg-rig-column-{rig_id}"')
        if rig_id < 4:
            column_end = camera_cfg_page.index(
                f'id="camcfg-rig-column-{rig_id + 1}"', column_start
            )
        else:
            column_end = camera_cfg_page.index('</section>', column_start)
        column = camera_cfg_page[column_start:column_end]

        assert re.search(
            rf'<button\b(?=[^>]*class="[^"]*\brig-preview-button\b[^"]*")'
            rf'(?=[^>]*type="button")(?=[^>]*data-rig-id="{rig_id}")[^>]*>'
            rf'\s*Preview\s*</button>',
            column,
        )
        assert re.search(
            rf'<div\b(?=[^>]*class="[^"]*\brig-preview\b[^"]*")'
            rf'(?=[^>]*id="rig-preview-{rig_id}")[^>]*>',
            column,
        )


def test_updateRigs_toggles_camcfg_columns():
    update_rigs = re.search(
        r"function updateRigs\(rigs\)\s*\{(?P<body>.*?)\n\}"
        r"\s*\n\s*document\.addEventListener",
        INDEX_HTML,
        flags=re.DOTALL,
    )
    assert update_rigs, "updateRigs function is missing"

    body = update_rigs.group("body")
    assert re.search(
        r"getElementById\(`camcfg-rig-column-\$\{defaultRig\.rig_id\}`\)",
        body,
    )

    assert (
        "const triggerEnabled = "
        "defaultRig.rig_id === 1 || rig.enabled === true"
        in body
    )
    assert re.search(
        r"cameraColumn\.classList\.toggle\("
        r"'enabled',\s*triggerEnabled\)",
        body,
    )
    assert re.search(r"cameraColumn\.hidden\s*=\s*false", body)
    assert re.search(
        r"cameraColumn\.querySelector\('\.rig-preview-button'\)",
        body,
    )
    assert re.search(r"previewButton\.disabled\s*=\s*false", body)
    assert not re.search(r"\bset(?:Interval|Timeout)\s*\(", body)

