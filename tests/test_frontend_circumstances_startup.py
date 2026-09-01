from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1]
    / "flask_app"
    / "templates"
    / "index.html"
).read_text(encoding="utf-8")


def test_saved_circumstances_are_loaded_on_frontend_startup():
    assert (
        "try { refreshSavedCircumstances(); } "
        "catch(e) { console.error('refreshSavedCircumstances', e); }"
    ) in INDEX
