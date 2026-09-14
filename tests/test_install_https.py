from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "install" / "install_solareclipse.sh").read_text(encoding="utf-8")


def test_installer_creates_persistent_local_ca():
    assert 'TLS_DIR="$VAR_DIR/tls"' in INSTALLER
    assert 'SolarTrigger Local CA' in INSTALLER
    assert 'Existing SolarTrigger local CA retained.' in INSTALLER
    assert '-days 3650' in INSTALLER


def test_installer_serves_https_and_ca_bootstrap():
    assert 'listen 443 ssl;' in INSTALLER
    assert 'ssl_certificate     $TLS_SERVER_CERT;' in INSTALLER
    assert 'ssl_certificate_key $TLS_SERVER_KEY;' in INSTALLER
    assert 'location = /solartrigger-ca.crt' in INSTALLER
    assert 'return 301 https://\\$host\\$request_uri;' in INSTALLER


def test_server_certificate_contains_local_names_and_hotspot_ip():
    assert 'DNS:$NEW_HOSTNAME.local' in INSTALLER
    assert 'DNS:$DOMAIN' in INSTALLER
    assert 'IP:192.168.50.1' in INSTALLER
    assert 'extendedKeyUsage=serverAuth' in INSTALLER


def test_public_ca_is_servable_without_exposing_private_keys():
    assert 'chown root:root "$TLS_DIR"' in INSTALLER
    assert 'chmod 711 "$TLS_DIR"' in INSTALLER
    assert 'chmod 600 "$TLS_CA_KEY" "$TLS_SERVER_KEY"' in INSTALLER
    assert 'chmod 644 "$TLS_CA_CERT" "$TLS_SERVER_CERT" "$TLS_SERVER_EXT"' in INSTALLER
