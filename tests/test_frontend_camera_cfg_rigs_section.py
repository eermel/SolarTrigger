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
    assert re.search(r"cameraColumn\.classList\.toggle\('enabled',\s*enabled\)", body)
    assert re.search(r"cameraColumn\.hidden\s*=\s*!enabled", body)
    assert re.search(r"const enabled\s*=\s*rig\.enabled\s*===\s*true", body)
    assert not re.search(r"\bset(?:Interval|Timeout)\s*\(", body)
