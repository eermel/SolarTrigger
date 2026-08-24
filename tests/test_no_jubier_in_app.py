import builtins
import importlib
from pathlib import Path

import pytest


APP_PATH = Path(__file__).parents[1] / "flask_app" / "app.py"


def test_flask_app_does_not_reference_jubier_calculator():
    app_source = APP_PATH.read_text(encoding="utf-8")

    assert "eclipse_calculator_jubier" not in app_source.lower()


def test_flask_app_does_not_reference_playwright_or_chromium():
    app_source = APP_PATH.read_text(encoding="utf-8").lower()

    assert "playwright" not in app_source
    assert "chromium" not in app_source


def test_flask_app_import_succeeds_when_playwright_is_unavailable(monkeypatch):
    real_import = builtins.__import__

    def import_without_playwright(name, *args, **kwargs):
        if name == "playwright" or name.startswith("playwright."):
            raise ImportError("simulated unavailable playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_playwright)

    try:
        module = importlib.reload(importlib.import_module("flask_app.app"))
    except ImportError as exc:
        pytest.fail(f"flask_app.app import failed without Playwright: {exc}")

    assert module.app is not None
