from tests.frontend_source import frontend_source
from pathlib import Path

INDEX = frontend_source()


def test_controls_default_to_rig_1_on_startup():
    assert "let selectedRigId = 1;" in INDEX


def test_load_eclipse_data_restores_saved_location():
    start = INDEX.index("async function loadEclipseData()")
    end = INDEX.index("async function loadCameraStatus()", start)
    block = INDEX[start:end]

    assert "applyCircumstancesLocationToForm(d);" in block


def test_load_saved_circumstances_has_single_eclipse_data_declaration():
    start = INDEX.index("async function loadSavedCircumstances(filename)")
    end = INDEX.index("async function saveEclipseConfig()", start)
    block = INDEX[start:end]

    assert block.count("const eclipseData = data.data || {};") == 1
    assert "applyCircumstancesLocationToForm(eclipseData);" in block
