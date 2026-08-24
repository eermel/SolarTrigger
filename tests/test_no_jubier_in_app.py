from pathlib import Path


def test_flask_app_does_not_reference_jubier_calculator():
    app_source = (Path(__file__).parents[1] / "flask_app" / "app.py").read_text(
        encoding="utf-8"
    )

    assert "eclipse_calculator_jubier" not in app_source.lower()
