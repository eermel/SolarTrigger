import json
import zipfile

from backend.system_maintenance import validate_release_zip
from scripts import build_release_package


def _fake_repo(tmp_path):
    root = tmp_path / "repo"
    for directory in build_release_package.COPY_TREES:
        if directory == "jubier_files":
            continue
        (root / directory).mkdir(parents=True, exist_ok=True)

    (root / "backend" / "runtime_daemon.py").write_text(
        "print('runtime')\n",
        encoding="utf-8",
    )
    (root / "services" / "__init__.py").write_text("", encoding="utf-8")
    (root / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    (root / "scripts").mkdir()
    for script_name in build_release_package.RUNTIME_SCRIPTS:
        (root / "scripts" / script_name).write_text(
            "print('runtime')\n",
            encoding="utf-8",
        )
    (root / "scripts" / "measure_camera_wakeup.py").write_text(
        "print('dev only')\n",
        encoding="utf-8",
    )
    (root / "configs" / "photo_cfg").mkdir()
    (root / "configs" / "photo_cfg" / "photo_default.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (root / "Sounds" / "contact.wav").write_bytes(b"RIFF")
    (root / "install" / "solartrigger-release-update").write_text(
        "#!/bin/sh\n",
        encoding="utf-8",
    )
    (root / "data" / "sample.dat").write_text("data\n", encoding="utf-8")
    (root / "vendor" / "README").write_text("vendor\n", encoding="utf-8")

    flask = root / "flask_app"
    (flask / "templates").mkdir(parents=True)
    (flask / "static" / "js").mkdir(parents=True)
    (flask / "static" / "css").mkdir(parents=True)
    (flask / "app.py").write_text("app = object()\n", encoding="utf-8")
    (flask / "templates" / "index.html").write_text(
        "<html></html>\n",
        encoding="utf-8",
    )
    (flask / "static" / "js" / "solartrigger.js").write_text(
        "console.log('ok');\n",
        encoding="utf-8",
    )
    (flask / "static" / "css" / "solartrigger.css").write_text(
        "body{}\n",
        encoding="utf-8",
    )
    return root


def test_build_release_package_matches_runtime_layout(tmp_path):
    root = _fake_repo(tmp_path)
    output = tmp_path / "solartrigger-1.2.zip"

    result = build_release_package.build_release(
        root,
        output,
        "1.2",
        build_commit="c" * 40,
    )

    assert result == output.resolve()
    manifest = validate_release_zip(output)
    assert manifest["version"] == "1.2"
    assert manifest["build_commit"] == "c" * 40

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "payload/app.py" in names
        assert "payload/wsgi.py" in names
        assert "payload/templates/index.html" in names
        assert "payload/static/js/solartrigger.js" in names
        assert "payload/static/sounds/contact.wav" in names
        assert "payload/BUILD_COMMIT" in names
        assert "payload/flask_app/app.py" not in names
        assert "payload/scripts/measure_camera_wakeup.py" not in names
        assert "payload/jubier_files/" not in names

        stored = json.loads(archive.read("manifest.json"))
        assert stored["schema_version"] == 2
        assert stored["files"]["app.py"]["size"] > 0


def test_build_release_rejects_unsafe_version(tmp_path):
    root = _fake_repo(tmp_path)

    try:
        build_release_package.build_release(
            root,
            tmp_path / "bad.zip",
            "../bad",
        )
    except ValueError as exc:
        assert "version" in str(exc)
    else:
        raise AssertionError("unsafe version was accepted")



def test_build_release_excludes_untracked_local_files(tmp_path, monkeypatch):
    root = _fake_repo(tmp_path)
    local_only = root / "configs" / "rig" / "default.json"
    local_only.parent.mkdir(parents=True)
    local_only.write_text('{"local": true}\n', encoding="utf-8")

    tracked = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != local_only
    }
    monkeypatch.setattr(
        build_release_package,
        "_git_tracked_files",
        lambda _root: tracked,
    )

    output = tmp_path / "solartrigger-1.3.zip"
    build_release_package.build_release(
        root,
        output,
        "1.3",
        build_commit="d" * 40,
    )

    with zipfile.ZipFile(output) as archive:
        assert "payload/configs/rig/default.json" not in archive.namelist()
