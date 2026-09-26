from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS = (
    ROOT / "flask_app" / "static" / "js" / "solartrigger.js"
).read_text(encoding="utf-8")
HTML = (
    ROOT / "flask_app" / "templates" / "index.html"
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
        "async function refreshRigDevices(silent = false, fullLegacyDetect = true) {",
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
        < characterize.index("await characterizationRequest(characterized ? 'recharacterize' : 'start'")
    )
    assert "select.disabled = true" in characterize



def test_camera_validation_ui_does_not_describe_plan_runtime():
    assert (
        "A successful characterization automatically starts end-to-end validation. "
        "RAW files remain on the card."
        in HTML
    )

    validation_js = _between(
        "// CAMERA VALIDATION — end-to-end real camera run",
        "// DEBUG TAB — UI adapter over the existing Trigger functionality",
    )
    assert "The validation report and run log will be kept for debugging." in validation_js



def test_characterization_and_validation_show_immediate_starting_feedback():
    characterization = _between(
        "async function startCameraCharacterization() {",
        "async function cancelCameraCharacterization() {",
    )
    assert "cameraCharacterizationStarting = true;" in characterization
    assert "Starting camera characterization…" in characterization
    assert (
        characterization.index("appendCameraAddLogLine(")
        < characterization.index("await waitForBrowserPaint()")
        < characterization.index("await characterizationRequest(characterized ? 'recharacterize' : 'start'")
    )

    validation = _between(
        "async function prepareCameraValidation() {",
        "async function cancelCameraValidation() {",
    )
    assert "cameraValidationStarting = true;" in validation
    assert "Preparing camera validation…" in validation
    assert (
        validation.index("start.disabled = true")
        < validation.index("await waitForBrowserPaint()")
        < validation.index("fetch('/api/camera-validation/prepare'")
    )
