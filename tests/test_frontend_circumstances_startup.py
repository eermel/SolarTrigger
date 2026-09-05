from tests.frontend_source import frontend_source
from pathlib import Path


INDEX = frontend_source()


def test_saved_circumstances_are_loaded_on_frontend_startup():
    assert (
        "try { refreshSavedCircumstances(); } "
        "catch(e) { console.error('refreshSavedCircumstances', e); }"
    ) in INDEX
