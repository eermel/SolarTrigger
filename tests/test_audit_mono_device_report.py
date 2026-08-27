from pathlib import Path


def test_audit_mono_device_report_exists_and_sections_present():
    report_path = Path("specs/FEAT-100-audit-mono-device.md")
    assert report_path.is_file()

    report = report_path.read_text(encoding="utf-8")
    expected_sections = (
        "Arborescence",
        "Hypothèses mono-device",
        "Chaîne photo",
        "Monture",
        "EAF",
        "Points communs avant divergence",
    )

    present_sections = sum(section in report for section in expected_sections)
    assert present_sections >= 4
