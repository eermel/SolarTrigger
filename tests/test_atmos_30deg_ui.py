from pathlib import Path


HTML = (
    Path(__file__).resolve().parents[1]
    / "flask_app/templates/index.html"
).read_text(encoding="utf-8")


def test_atmos_switch_documents_30_degree_application_limit():
    assert 'id="cfg-atmo-switch"' in HTML
    assert "Active only if Sun &lt; 30°" in HTML
