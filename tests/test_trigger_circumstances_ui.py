from pathlib import Path

from tests.frontend_source import frontend_source


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")
UI = frontend_source()


def test_trigger_partial_eclipse_accepts_missing_c2_c3():
    assert 'required = ("TSTART", "C1", "C4", "TEND")' in APP
    assert '(c2 is None) != (c3 is None)' in APP
    assert 'missing.append("C2/C3 pair")' in APP


def test_trigger_partial_circumstances_show_only_required_contacts():
    assert "if (isPartialTrigger)" in UI
    assert "contacts.find(c => c.key === 'TSTART')" in UI
    assert "contacts.find(c => c.key === 'C1')" in UI
    assert "key: 'TMAX'" in UI
    assert "contacts.find(c => c.key === 'C4')" in UI
    assert "contacts.find(c => c.key === 'TEND')" in UI


def test_trigger_total_eclipse_shows_two_diamond_ring_boundaries():
    assert "state.triggerDiamondDurationS" in UI
    assert "key: 'DR_C2'" in UI
    assert "key: 'DR_C3'" in UI
    assert UI.count("label: 'DIAMOND RING'") == 2

    assert "_toSec(c2Value) - diamondDuration" in UI
    assert "_toSec(c3Value) + diamondDuration" in UI


def test_trigger_diamond_duration_comes_from_selected_photo_setup():
    assert "/api/configs/load_photo/" in UI
    assert "data?.phases?.diamond_ring?.duration_s" in UI
    assert 'onchange="refreshTriggerCircumstancesForPhoto()"' in UI
