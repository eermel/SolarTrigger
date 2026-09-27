from tests.frontend_source import frontend_source


def test_system_tab_contains_camera_and_maintenance_tools():
    source = frontend_source()

    assert "<span>SYSTEM</span>" in source
    assert "<span>ADD CAMERA</span>" not in source

    assert "Characterize &amp; Validate" in source
    assert "Re-characterize Camera" not in source
    assert '<div class="card-title">Camera Validation</div>' not in source

    assert "Persistent data" in source
    assert "Update System" in source
    assert "Update Solar Eclipse Trigger" in source


def test_system_tab_has_camera_update_and_log_sections():
    source = frontend_source()

    camera = source.index('data-system-section="camera"')
    update = source.index('data-system-section="update"')
    log = source.index('data-system-section="log"')

    assert camera < update < log
    assert source.count('data-system-section=') == 3

    camera_section = source[camera:update]
    assert "Device discovery" in camera_section
    assert '<div class="card-title">Camera</div>' in camera_section

    update_section = source[update:log]
    assert "Persistent data" in update_section
    assert "Update System" in update_section
    assert "Update Solar Eclipse Trigger" in update_section

    log_end = source.index("</section>", log)
    log_section = source[log:log_end]
    assert '<div class="system-section-title">Log</div>' in log_section
    assert 'id="camera-add-log"' in log_section

    system_section = source[camera:log_end]
    assert r"\n" not in system_section


def test_system_sections_keep_outlines_with_devices_spacing():
    source = frontend_source()

    assert ".add-camera-section,\n.devices-section {" in source

    start = source.index(".system-section {")
    end = source.index("}", start)
    rule = source[start:end]

    assert "gap: 12px;" in rule
    assert "border: 1px solid var(--border);" in rule
    assert "border-radius: 8px;" in rule
    assert "padding: 14px;" in rule
    assert "margin-bottom:" not in rule


def test_system_camera_title_does_not_double_section_gap():
    source = frontend_source()

    start = source.index(".system-camera-group-title {")
    end = source.index("}", start)
    rule = source[start:end]

    assert "margin-bottom:" not in rule


def test_system_camera_section_has_no_legacy_extra_spacing():
    source = frontend_source()

    start = source.index(".system-camera-group {")
    end = source.index("}", start)
    rule = source[start:end]

    assert "margin: 0;" in rule
    assert "margin-bottom:" not in rule
    assert "padding:" not in rule
    assert "border:" not in rule


def test_system_update_is_single_full_width_action():
    source = frontend_source()

    assert 'id="system-check-update"' in source
    assert "CHECK AND UPDATE" in source
    assert "checkAndUpdateSystem()" in source
    assert "onclick=\"checkSystemUpdates()\"" not in source
    assert "onclick=\"updateSystem()\"" not in source
    assert "/api/system/maintenance/update-system" in source


def test_application_update_uses_one_validate_install_reboot_action():
    source = frontend_source()

    assert 'class="system-update-primary"' in source
    assert 'id="solartrigger-update-file"' in source
    assert 'id="solartrigger-validate-install-release"' in source
    assert "Validate &amp; Install Update &amp; Reboot" in source
    assert "validateInstallSolarTriggerRelease()" in source
    assert "uploadSolarTriggerRelease()" not in source
    assert "installSolarTriggerRelease()" not in source
    assert 'id="solartrigger-install-release"' not in source
    assert "Validate package" not in source
    assert ">Install update<" not in source
    assert "Rollback" in source


def test_application_update_validates_before_installing():
    source = frontend_source()

    function_start = source.index(
        "async function validateInstallSolarTriggerRelease()"
    )
    rollback_start = source.index(
        "async function rollbackSolarTriggerRelease()",
        function_start,
    )
    function = source[function_start:rollback_start]

    validate_pos = function.index("/api/system/maintenance/upload-release")
    token_pos = function.index("const uploadToken = validation.upload_token")
    install_pos = function.index("/api/system/maintenance/install-release")

    assert validate_pos < token_pos < install_pos
    assert "if (!validationResponse.ok)" in function
    assert "{upload_token: uploadToken}" in function
    assert "Raspberry Pi will reboot" in function


def test_application_update_reports_non_json_http_errors():
    source = frontend_source()

    helper_start = source.index("async function maintenanceJsonResponse(response)")
    helper_end = source.index("async function maintenancePost(url, body)", helper_start)
    helper = source[helper_start:helper_end]
    assert "const responseText = await response.text();" in helper
    assert "JSON.parse(responseText)" in helper
    assert "response.statusText" in helper
    assert "invalid server response" in helper

    function_start = source.index(
        "async function validateInstallSolarTriggerRelease()"
    )
    rollback_start = source.index(
        "async function rollbackSolarTriggerRelease()",
        function_start,
    )
    function = source[function_start:rollback_start]
    assert "maintenanceJsonResponse(validationResponse)" in function
    assert "validationResponse.json()" not in function


def test_system_maintenance_cards_have_requested_colours():
    source = frontend_source()

    assert "system-maintenance-card--orange" in source
    assert "system-maintenance-card--blue" in source


def test_add_camera_uses_unified_characterize_validate_action():
    source = frontend_source()
    assert "Characterize &amp; Validate" in source
    assert "startCameraCharacterization" in source
    assert "/api/camera-characterization/recharacterize" in source
    assert "startAutomaticCameraValidation" in source


def test_system_page_uses_one_shared_log():
    source = frontend_source()

    assert "<span>Camera log</span>" not in source
    assert "<span>Log</span>" in source

    assert 'id="camera-add-log"' in source
    assert 'id="system-update-log"' not in source
    assert 'id="solartrigger-update-log"' not in source


def test_maintenance_uses_shared_system_log():
    source = frontend_source()

    assert "updateCameraAddLog('systemUpdate', lines)" in source
    assert "updateCameraAddLog('solarTriggerUpdate', lines)" in source

    assert "=== UPDATE SYSTEM ===" in source
    assert "=== UPDATE SOLAR ECLIPSE TRIGGER ===" in source

    assert "getElementById('system-update-log')" not in source
    assert "getElementById('solartrigger-update-log')" not in source


def test_clear_system_log_includes_maintenance():
    source = frontend_source()

    assert "cameraAddLogState.systemUpdateOffset =" in source
    assert "cameraAddLogState.solarTriggerUpdateOffset =" in source



def test_system_update_button_tracks_ethernet_link():
    source = frontend_source()

    assert "CHECK AND UPDATE SYSTEM" in source
    assert "Eth need to be connected" in source
    assert "button.disabled = Boolean(data.running) || !ethernetConnected" in source
    assert "data.ethernet && data.ethernet.connected" in source


def test_system_update_starts_without_confirmation_popup():
    source = frontend_source()
    start = source.index("async function checkAndUpdateSystem()")
    end = source.index(
        "async function validateInstallSolarTriggerRelease()",
        start,
    )
    function = source[start:end]

    assert "confirm(" not in function
    assert "/api/system/maintenance/update-system" in function


def test_system_update_polls_logs_only_while_maintenance_is_running():
    source = frontend_source()

    assert "const MAINTENANCE_POLL_INTERVAL_MS = 1000;" in source
    assert "async function pollMaintenanceStatus()" in source
    assert "if (data && data.running)" in source
    assert "scheduleMaintenancePoll();" in source
    assert "stopMaintenancePolling();" in source
    assert "startMaintenancePolling();" in source
    assert "setInterval(loadMaintenanceStatus" not in source


def test_system_update_has_no_network_status_line():
    source = frontend_source()

    assert 'id="system-update-network"' not in source
    assert "Ethernet: checking" not in source


def test_system_update_has_no_apt_explanatory_text():
    source = frontend_source()

    assert "Physical Ethernet required." not in source



def test_system_camera_group_contains_camera_workflow():
    source = frontend_source()
    assert 'class="system-section system-camera-group"' in source
    assert 'data-system-section="camera"' in source
    assert 'class="system-camera-group-title">Camera</div>' in source
    group_start = source.index('data-system-section="camera"')
    update_start = source.index('data-system-section="update"')
    camera_group = source[group_start:update_start]
    assert '<div class="card-title">Device discovery</div>' in camera_group
    assert '<div class="card-title">Camera</div>' in camera_group
    assert "Characterize &amp; Validate" in camera_group
    assert "Re-characterize Camera" not in camera_group
    assert '<div class="card-title">Camera Validation</div>' not in camera_group


def test_unified_camera_select_has_chevron():
    source = frontend_source()
    marker = 'id="camera-characterization-select"'
    start = source.index(marker)
    select = source[start:source.index(">", start)]
    assert "file-select-chevron" in select


def test_release_rollback_can_select_any_installed_version():
    source = frontend_source()

    assert 'id="solartrigger-rollback-version"' in source
    assert 'id="solartrigger-rollback-release"' in source
    assert "renderInstalledSolarTriggerReleases" in source
    assert "releaseState.releases" in source
    assert "rollback_eligible !== false" in source
    assert (
        "maintenancePost(" in source
        and "/api/system/maintenance/rollback-release" in source
        and "{version}" in source
    )


def test_release_actions_warn_that_pi_reboots():
    source = frontend_source()

    assert "Raspberry Pi will reboot" in source
    assert "Pi reboot requested" in source

def test_camera_maintenance_button_uses_unified_selection_without_refresh():
    source = frontend_source()
    start = source.index("function renderCameraCharacterizationStatus(status)")
    end = source.index("async function pollCameraCharacterization()", start)
    characterization = source[start:end]
    assert "status.qualification_candidates" in characterization
    assert "option.dataset.characterized" in characterization
    assert "camera-characterization-start" in characterization
