from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS = (
    ROOT / "flask_app" / "static" / "js" / "solartrigger.js"
).read_text(encoding="utf-8")


def _between(start, end):
    assert start in JS
    assert end in JS
    return JS.split(start, 1)[1].split(end, 1)[0]


def test_mount_tracking_switch_waits_for_authoritative_status():
    assert "let trackingCommandPending = false;" in JS

    display = _between(
        "function displayMount(data) {",
        "async function refreshMount() {",
    )
    assert "trackingEnabled = data && data.tracking_enabled === true;" in display
    assert "trackingSwitch.checked = trackingEnabled;" in display
    assert "trackingCommandPending" in display

    handler = _between(
        "trackingSwitch.addEventListener('change', async () => {",
        "document.addEventListener('controlsrigchange'",
    )
    assert "const requestedTracking = trackingSwitch.checked;" in handler
    assert "trackingSwitch.checked = trackingEnabled;" in handler
    assert "trackingCommandPending = true;" in handler
    assert "trackingSwitch.disabled = true;" in handler
    assert "await postMount(" in handler
    assert "trackingCommandPending = false;" in handler
    assert "await refreshMount();" in handler


def test_add_camera_long_actions_disable_before_network_work():
    refresh = _between(
        "async function refreshRigDevices(silent = false) {",
        "let cameraCharacterizationQuestion = null;",
    )
    assert (
        refresh.index("button.disabled = true")
        < refresh.index("await waitForBrowserPaint()")
        < refresh.index("fetch('/api/rigs/devices/refresh'")
    )

    characterize = _between(
        "async function startCameraCharacterization() {",
        "async function cancelCameraCharacterization() {",
    )
    assert (
        characterize.index("button.disabled = true")
        < characterize.index("await waitForBrowserPaint()")
        < characterize.index("await characterizationRequest('start'")
    )
    assert "select.disabled = true" in characterize


def test_sequence_generation_logs_success_after_completion():
    assert (
        "`RIG ${rigId}: sequence generated successfully`"
        in JS
    )
    assert (
        "appendSequencerLog(\n"
        "      `RIG ${rigId}: sequence generated successfully`,\n"
        "      'success'\n"
        "    );"
        in JS
    )
