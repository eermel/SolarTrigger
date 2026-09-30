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


def test_trigger_dryrun_countdown_ignores_source_calendar_date():
    assert "const clockOnlyCountdown = !triggerButtonIsRealDate();" in UI
    assert "if (!clockOnlyCountdown && eclipseDateUtc)" in UI
    assert "DRY-RUN (and legacy date-less JSON): compare UTC clock time only." in UI


def test_trigger_start_reloads_diamond_duration_before_preflight():
    start = UI.index("async function startTrigger()")
    end = UI.index("async function startDebug()", start)
    source = UI[start:end]

    load_index = source.index(
        "await loadTriggerDiamondDuration(inputs.photo_file);"
    )
    preflight_index = source.index(
        "const preflightTokens = await preflightTriggerRigs(rigIds, inputs);"
    )

    assert load_index < preflight_index
    assert "renderContacts(state.triggerCircumstances);" in source


def test_trigger_countdown_timer_prefers_trigger_circumstances():
    assert (
        "const countdownCircumstances = state.triggerCircumstances || state.eclipse;"
        in UI
    )
    assert (
        "if (countdownCircumstances) updateCountdowns(countdownCircumstances);"
        in UI
    )
