from pathlib import Path


def test_trigger_and_add_camera_file_selects_share_config_style():
    html = Path("flask_app/templates/index.html").read_text(encoding="utf-8")
    for select_id in (
        "camera-characterization-select",
        "camera-validation-select",
        "trigger-circumstances-select",
        "trigger-photo-select",
        "trigger-exposure-opt-select",
    ):
        fragment = html.split(f'id="{select_id}"', 1)[1].split(">", 1)[0]
        class_attr = fragment.split('class="', 1)[1].split('"', 1)[0]
        assert "config-file-select" in class_attr.split()

    css = Path("flask_app/static/css/solartrigger.css").read_text(encoding="utf-8")
    assert ".config-file-select {" in css
    assert ".log-line.purple" in css
    assert ".log-line.totality" in css
