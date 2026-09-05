import re

from tests.frontend_source import frontend_source


HTML = frontend_source()


def test_tab_icons_and_labels_are_clickable():
    # Les enfants du bouton doivent rester des cibles normales.
    # Leur clic remonte naturellement au <button class="tab">.
    assert "#tabs .tab > svg," not in HTML
    assert "#tabs .tab > span {" not in HTML

    tabs = re.findall(
        r'<button\b[^>]*class="[^"]*\btab\b[^"]*"'
        r'[^>]*onclick="showTab\([^)]*\)"[^>]*>',
        HTML,
    )

    assert len(tabs) == 9


def test_flash_notification_never_blocks_tab_clicks():
    start = HTML.index(".flash {")
    end = HTML.index("}", start)
    flash_css = HTML[start:end]

    assert "position: fixed;" in flash_css
    assert "z-index: 100;" in flash_css
    assert "pointer-events: none;" in flash_css
