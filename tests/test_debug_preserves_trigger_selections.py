from pathlib import Path


JS = Path("flask_app/static/js/solartrigger.js")


def _start_debug_source():
    source = JS.read_text(encoding="utf-8")
    start = source.index("async function startDebug()")
    end = source.index("\n\nasync function startDryRun()", start)
    return source[start:end]


def test_debug_does_not_replace_normal_circumstances_selection():
    debug = _start_debug_source()

    assert "trigger-circumstances-select" not in debug
    assert "select.value = displayed.filename" not in debug
    assert "displayed.circumstances" in debug
    assert "renderContacts(displayed.circumstances)" in debug


def test_debug_reads_normal_photo_and_exposure_without_mutating_selects():
    debug = _start_debug_source()

    assert "const inputs = selectedTriggerInputs();" in debug
    assert "photo_file: inputs.photo_file" in debug
    assert "exposure_opt_file: inputs.exposure_opt_file" in debug
    assert "trigger-photo-select" not in debug
    assert "trigger-exposure-opt-select" not in debug
