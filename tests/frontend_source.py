from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

HTML_PATH = ROOT / "flask_app" / "templates" / "index.html"
JS_PATH = ROOT / "flask_app" / "static" / "js" / "solartrigger.js"
CSS_PATH = ROOT / "flask_app" / "static" / "css" / "solartrigger.css"


def frontend_source():
    """Vue source historique utilisée par les tests frontend.

    Les assets peuvent être physiquement séparés en HTML / JS / CSS,
    tandis que les anciens tests continuent à inspecter l'ensemble
    comme lorsque tout se trouvait dans index.html.
    """
    parts = [
        HTML_PATH.read_text(encoding="utf-8"),
        JS_PATH.read_text(encoding="utf-8"),
        CSS_PATH.read_text(encoding="utf-8"),
    ]

    return "\n".join(parts)
