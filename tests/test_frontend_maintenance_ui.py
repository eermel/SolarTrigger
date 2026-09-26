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
    assert 'class="system-section-title">Log</div>' in source

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
    assert "button.disabled = !ethernetConnected" in source
    assert "data.ethernet && data.ethernet.connected" in source


def test_system_update_has_no_network_status_line():
    source = frontend_source()

    assert 'id="system-update-network"' not in source
    assert "Ethernet: checking" not in source


def test_system_update_has_no_apt_explanatory_text():
    source = frontend_source()

    assert "Physical Ethernet required." not in source



def test_system_page_has_camera_update_and_log_sections():
    source = frontend_source()

    camera_start = source.index('data-system-section="camera"')
    update_start = source.index('data-system-section="update"')
    log_start = source.index('data-system-section="log"')

    assert camera_start < update_start < log_start
    assert 'class="system-section-title">Camera</div>' in source[camera_start:update_start]
    assert 'class="system-section-title">Update</div>' in source[update_start:log_start]
    assert 'class="system-section-title">Log</div>' in source[log_start:]


def test_system_camera_section_contains_camera_workflow_only():
    source = frontend_source()
    camera_start = source.index('data-system-section="camera"')
    update_start = source.index('data-system-section="update"')
    camera_section = source[camera_start:update_start]

    assert '<div class="card-title">Device discovery</div>' in camera_section
    assert '<div class="card-title">Camera</div>' in camera_section
    assert "Characterize &amp; Validate" in camera_section
    assert "Persistent data" not in camera_section
    assert "Update System" not in camera_section


def test_system_update_section_contains_maintenance_tools():
    source = frontend_source()
    update_start = source.index('data-system-section="update"')
    log_start = source.index('data-system-section="log"')
    update_section = source[update_start:log_start]

    assert '<div class="card-title">Persistent data</div>' in update_section
    assert '<div class="card-title">Update System</div>' in update_section
    assert '<div class="card-title">Update Solar Eclipse Trigger</div>' in update_section
    assert 'id="camera-add-log"' not in update_section


def test_system_log_section_contains_shared_log():
    source = frontend_source()
    log_start = source.index('data-system-section="log"')
    log_section = source[log_start:]

    assert 'id="camera-add-log"' in log_section
    assert "clearCameraAddLog()" in log_section



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
