from tests.frontend_source import frontend_source
from pathlib import Path

INDEX = frontend_source()


def test_devices_rig_switches_have_no_visible_on_off_labels():
    start = INDEX.index('<div class="page active" id="devices-panel">')
    end = INDEX.index('</section>', start)
    block = INDEX[start:end]

    for rig_id in range(1, 5):
        assert f'<label for="rig-switch-{rig_id}">OFF</label>' not in block
        assert f'<label for="rig-switch-{rig_id}">ON</label>' not in block


def test_devices_rigs_have_persistent_name_field():
    start = INDEX.index("function renderRigDevices(")
    end = INDEX.index("async function loadRigDevices(", start)
    block = INDEX[start:end]

    assert 'class="field rig-name-field"' in block
    assert ">RIG NAME</label>" in block
    assert 'id="rig-${rigId}-name"' in block
    assert 'onchange="persistRigName(${rigId}, this)"' in block


def test_rig_name_is_saved_through_existing_rig_devices_api():
    start = INDEX.index("async function persistRigName(")
    end = INDEX.index("async function selectRigDevice(", start)
    block = INDEX[start:end]

    assert "fetch('/api/rigs/devices'" in block
    assert "rigs: [{rig_id: rigId, name: name}]" in block
    assert "await loadRigDevices()" in block


def test_rig_name_is_propagated_to_photo_config_and_camera():
    start = INDEX.index("function updateRigs(rigs)")
    end = INDEX.index("document.addEventListener('change'", start)
    block = INDEX[start:end]

    assert "title.textContent" in block
    assert "`${defaultName} — ${rigName}`" in block

    assert "cameraRigColumn.querySelector('.cam-rig-name')" in block
    assert "nameElement.textContent = rigName" in block
