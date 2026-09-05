import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

INDEX_PATH = ROOT / "flask_app" / "templates" / "index.html"
SOCKET_PATH = ROOT / "flask_app" / "static" / "js" / "socket.io.min.js"

HTML = INDEX_PATH.read_text(encoding="utf-8")


def test_socketio_is_served_as_local_static_asset():
    assert SOCKET_PATH.is_file()

    socket_code = SOCKET_PATH.read_text(encoding="utf-8")

    assert len(socket_code) > 40000
    assert socket_code.lstrip().startswith("!function(")

    assert (
        "/static/js/socket.io.min.js"
        in HTML
    )

    # La bibliothèque minifiée ne doit plus être embarquée dans index.html.
    assert "!function(t,n)" not in HTML


def test_solartrigger_javascript_is_served_as_local_static_asset():
    solartrigger_path = (
        ROOT / "flask_app" / "static" / "js" / "solartrigger.js"
    )

    assert solartrigger_path.is_file()

    code = solartrigger_path.read_text(encoding="utf-8")

    assert len(code) > 150000
    assert "function showTab(" in code
    assert "function flash(" in code

    assert (
        "/static/js/solartrigger.js"
        in HTML
    )


def test_no_application_javascript_remains_inline():
    scripts = re.findall(
        r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>",
        HTML,
        re.S | re.I,
    )

    inline_scripts = [
        body
        for attrs, body in scripts
        if not re.search(r"\bsrc\s*=", attrs, re.I)
        and body.strip()
    ]

    assert inline_scripts == []


def test_socketio_is_loaded_before_solartrigger():
    socket_pos = HTML.index("js/socket.io.min.js")
    app_pos = HTML.index("js/solartrigger.js")

    assert socket_pos < app_pos

def test_stylesheet_is_served_as_local_static_asset():
    css_path = (
        ROOT / "flask_app" / "static" / "css" / "solartrigger.css"
    )

    assert css_path.is_file()

    css = css_path.read_text(encoding="utf-8")

    assert len(css) > 20000
    assert ":root" in css
    assert ".btn" in css
    assert ".flash" in css

    assert (
        "/static/css/solartrigger.css"
        in HTML
    )


def test_no_stylesheet_remains_inline():
    assert "<style" not in HTML.lower()
    assert "</style>" not in HTML.lower()

def test_installer_copies_frontend_static_assets():
    installer = (
        ROOT / "install" / "install_solareclipse.sh"
    ).read_text(encoding="utf-8")

    assert '"$PACKAGE_DIR/flask_app/static/js"' in installer
    assert '"$PACKAGE_DIR/flask_app/static/css"' in installer

    assert (
        'cp -a "$PACKAGE_DIR/flask_app/static/js/." '
        '"$APP_DIR/static/js/"'
        in installer
    )
    assert (
        'cp -a "$PACKAGE_DIR/flask_app/static/css/." '
        '"$APP_DIR/static/css/"'
        in installer
    )


def test_deploy_script_syncs_frontend_static_assets():
    deploy = (
        ROOT / "tools" / "deploy-prod.sh"
    ).read_text(encoding="utf-8")

    assert '"$SRC/flask_app/static/js"' in deploy
    assert '"$SRC/flask_app/static/css"' in deploy

    assert '"$SRC/flask_app/static/js/"' in deploy
    assert '"$DST_HOST:$DST/static/js/"' in deploy

    assert '"$SRC/flask_app/static/css/"' in deploy
    assert '"$DST_HOST:$DST/static/css/"' in deploy


def test_raw_index_contains_no_jinja_expressions():
    # La route "/" sert index.html avec send_from_directory(), pas render_template().
    assert "{{" not in HTML
    assert "{%" not in HTML
