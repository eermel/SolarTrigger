from pathlib import Path


JS = Path("flask_app/static/js/solartrigger.js")


def test_trigger_phase_event_synchronizes_running_state():
    source = JS.read_text(encoding="utf-8")

    handler_start = source.index("socket.on('trigger_phase', d => {")
    handler_end = source.index("\n});", handler_start) + len("\n});")
    handler = source[handler_start:handler_end]

    assert "const nextPhase = d.phase || 'idle';" in handler
    assert "phase: nextPhase" in handler
    assert "running: nextPhase !== 'idle'" in handler


def test_start_lock_uses_running_state():
    source = JS.read_text(encoding="utf-8")

    assert "function anyActiveTriggerRunning()" in source
    assert "rigState.running === true" in source
    assert "btnStart.disabled  = triggerStartLocked" in source
