import re

from tests.frontend_source import frontend_source


SOURCE = frontend_source()


def _photo_setup_section():
    start = SOURCE.index("<!-- ═══════════════ PAGE 2 : CFG PHOTO ═══════════════ -->")
    end = SOURCE.index("<!-- ═══════════════ EXPOSURE OPTIMIZATION ═══════════════ -->")
    return SOURCE[start:end]


def test_diamond_ring_overlap_allows_two_seconds():
    section = _photo_setup_section()

    assert re.search(
        r'id="cfg-dr-overlap"[^>]*min="2"',
        section,
    )
    assert "Minimum: 2 s." in section


def test_all_photo_setup_selects_use_uniform_chevron():
    section = _photo_setup_section()
    selects = re.findall(r"<select\s+([^>]+)>", section)

    cfg_selects = [
        attrs for attrs in selects
        if re.search(r'id="cfg-[^"]+"', attrs)
    ]
    assert cfg_selects
    assert all("file-select-chevron" in attrs for attrs in cfg_selects)


def test_iso_selects_use_same_field_height_as_other_selects():
    section = _photo_setup_section()

    for identifier in ("cfg-partial-iso", "cfg-dr-iso", "cfg-tot-iso"):
        match = re.search(
            rf'<select\s+[^>]*id="{identifier}"[^>]*>',
            section,
        )
        assert match
        assert "style=" not in match.group(0)
        assert "file-select-chevron" in match.group(0)


def test_partial_phase_uses_trigger_log_symbol():
    section = _photo_setup_section()

    assert "🌙 PARTIAL PHASE" in section
    assert "☀ PARTIAL PHASE" not in section
