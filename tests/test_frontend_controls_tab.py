from html.parser import HTMLParser
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "flask_app" / "templates" / "index.html").read_text(encoding="utf-8")


class _NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.container = None
        self._container_depth = 0
        self.tabs = []
        self.pages = []
        self._tab = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element_id = attributes.get("id")
        classes = attributes.get("class", "").split()

        if tag == "div" and element_id in {"tabs", "pages"} and self.container is None:
            self.container = element_id
            self._container_depth = 1
        elif tag == "div" and self.container is not None:
            self._container_depth += 1

        if self.container == "tabs" and tag == "button" and "tab" in classes:
            self._tab = {
                "id": element_id,
                "onclick": attributes.get("onclick"),
                "text": [],
            }
            self.tabs.append(self._tab)
        elif self.container == "pages" and tag == "div" and "page" in classes:
            self.pages.append(element_id)

    def handle_endtag(self, tag):
        if tag == "button":
            self._tab = None
        elif tag == "div" and self.container is not None:
            self._container_depth -= 1
            if self._container_depth == 0:
                self.container = None

    def handle_data(self, data):
        if self._tab is not None:
            self._tab["text"].append(data)


def test_controls_tab_and_panel_are_in_the_seven_item_navigation_order():
    parser = _NavigationParser()
    parser.feed(INDEX)

    labels = [" ".join(tab["text"]).strip() for tab in parser.tabs]
    assert labels == [
        "DEVICES",
        "SYNC GPS",
        "ÉCLIPSE",
        "CFG PHOTO",
        "CAMÉRA",
        "CONTROLS",
        "TRIGGER",
    ]
    assert parser.pages == [
        "devices-panel",
        "page-0",
        "page-1",
        "page-2",
        "page-3",
        "controls-panel",
        "page-4",
    ]

    controls, trigger = parser.tabs[5:]
    assert controls["id"] == "controls-tab"
    assert controls["onclick"] == "showTab(5)"
    assert trigger["onclick"] == "showTab(6)"


def test_trigger_initialization_uses_trigger_tab_index():
    trigger_initialization = re.compile(
        r"if\s*\(\s*n\s*===\s*6\s*\)\s*\{\s*"
        r"loadTriggerConfigList\(\);\s*loadEclipseFileList\(\);\s*\}"
    )

    assert trigger_initialization.search(INDEX)


def test_mount_section_is_unique_and_inside_controls_panel():
    controls_start = INDEX.index('<div class="page" id="controls-panel"')
    trigger_start = INDEX.index('<!-- ═══════════════ PAGE 4 : TRIGGER ═══════════════ -->')
    controls_panel = INDEX[controls_start:trigger_start]

    assert 'id="mount-section"' in controls_panel
    assert len(re.findall(r'id=["\']mount-section["\']', INDEX)) == 1
