from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "flask_app" / "templates" / "index.html").read_text(
    encoding="utf-8"
)


class _TabsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tabs = []
        self._tab = None
        self._tab_child_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()

        if tag == "button" and "tab" in classes:
            self._tab = {
                "onclick_attributes": [
                    value for name, value in attrs if name == "onclick"
                ],
                "label": [],
                "direct_children_with_onclick": [],
            }
            self.tabs.append(self._tab)
            self._tab_child_depth = 0
        elif self._tab is not None:
            if self._tab_child_depth == 0 and tag in {"svg", "span"}:
                if "onclick" in attributes:
                    self._tab["direct_children_with_onclick"].append(tag)
            self._tab_child_depth += 1

    def handle_endtag(self, tag):
        if self._tab is None:
            return
        if tag == "button" and self._tab_child_depth == 0:
            self._tab = None
        else:
            self._tab_child_depth -= 1

    def handle_data(self, data):
        if self._tab is not None:
            self._tab["label"].append(data)


def test_tabs_have_one_parent_click_handler_and_no_child_click_handler():
    parser = _TabsParser()
    parser.feed(INDEX)

    assert [" ".join(tab["label"]).strip() for tab in parser.tabs] == [
        "DEVICES",
        "SYNC GPS",
        "ECLIPSE",
        "PHOTO CFG",
        "CAMERA",
        "CONTROLS",
        "TRIGGER",
    ]
    assert [tab["onclick_attributes"] for tab in parser.tabs] == [
        [f"showTab({index})"] for index in range(7)
    ]
    assert all(not tab["direct_children_with_onclick"] for tab in parser.tabs)
