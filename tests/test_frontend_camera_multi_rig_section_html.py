from html.parser import HTMLParser
from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


class _CameraRigsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_camera_page = False
        self.in_rigs_section = False
        self._camera_page_depth = 0
        self._section_depth = 0
        self._current_column = None
        self.columns = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()

        if tag == "div" and attributes.get("id") == "page-3":
            self.in_camera_page = True
            self._camera_page_depth = 1
        elif tag == "div" and self.in_camera_page:
            self._camera_page_depth += 1

        if (
            tag == "section"
            and self.in_camera_page
            and "camera-rigs-section" in classes
        ):
            self.in_rigs_section = True
            self._section_depth = 1
        elif self.in_rigs_section:
            self._section_depth += 1

        if self.in_rigs_section and tag == "div" and "cam-rig-column" in classes:
            self._current_column = {
                "id": attributes.get("id"),
                "rig_id": attributes.get("data-rig-id"),
                "classes": set(),
                "buttons": [],
                "text": [],
            }
            self.columns.append(self._current_column)
        elif self._current_column is not None:
            self._current_column["classes"].update(classes)
            if tag == "button":
                self._current_column["buttons"].append([])

    def handle_endtag(self, tag):
        if self.in_rigs_section:
            self._section_depth -= 1
            if self._section_depth == 0:
                self.in_rigs_section = False
                self._current_column = None

        if tag == "div" and self.in_camera_page:
            self._camera_page_depth -= 1
            if self._camera_page_depth == 0:
                self.in_camera_page = False

    def handle_data(self, data):
        if self._current_column is None:
            return
        text = " ".join(data.split())
        if not text:
            return
        self._current_column["text"].append(text)
        if self._current_column["buttons"]:
            self._current_column["buttons"][-1].append(text)


def test_camera_page_contains_four_complete_rig_columns():
    parser = _CameraRigsParser()
    parser.feed(INDEX)

    assert len(parser.columns) == 4
    for rig_id, column in enumerate(parser.columns, start=1):
        assert column["id"] == f"cam-rig-column-{rig_id}"
        assert column["rig_id"] == str(rig_id)
        assert [" ".join(parts) for parts in column["buttons"]] == [
            "Read information",
            "Test photo",
        ]
        assert {
            "cam-rig-name",
            "cam-rig-binding",
            "cam-rig-status",
            "cam-rig-last-read",
        } <= column["classes"]
        text = " ".join(column["text"])
        assert f"RIG {rig_id}" in text
        assert "Name / alias" in text
        assert "Assigned camera" in text
        assert "Known status" in text
        assert "Last read" in text
