import re
from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def test_atmospheric_attenuation_card_is_unique_and_correctly_placed():
    title = '<div class="card-title">Atmospheric Attenuation</div>'
    camera_title = '<div class="card-title">Camera configuration</div>'
    partial_title = '<div class="card-title" style="color:var(--purple)">☀ PARTIAL PHASE</div>'

    assert INDEX.count(title) == 1
    assert INDEX.index(camera_title) < INDEX.index(title) < INDEX.index(partial_title)


def test_atmospheric_attenuation_card_has_on_off_switch():
    card = re.search(
        r'<div class="card">\s*'
        r'<div class="card-title">Atmospheric Attenuation</div>'
        r'(.*?)\s*</div>',
        INDEX,
        re.DOTALL,
    )

    assert card is not None
    control = card.group(1)
    assert 'class="focuser-mode-switch"' in control
    assert re.search(
        r'<input\s+type="checkbox"\s+id="cfg-atmo-switch"\s+role="switch"(?=\s|>)',
        control,
    )
    assert '<label for="cfg-atmo-switch">OFF</label>' in control
    assert '<label for="cfg-atmo-switch">ON</label>' in control
