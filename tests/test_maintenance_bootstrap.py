from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = (
    ROOT / "install" / "install_maintenance_helpers.sh"
).read_text(encoding="utf-8")
ROUTES = (
    ROOT / "backend" / "system_maintenance_routes.py"
).read_text(encoding="utf-8")


def test_existing_trigger_bootstrap_installs_root_helpers_and_sudoers():
    assert 'install -o root -g root -m 0755' in BOOTSTRAP
    assert '/usr/local/sbin/$HELPER' in BOOTSTRAP
    assert 'solartrigger-system-update' in BOOTSTRAP
    assert 'solartrigger-release-update' in BOOTSTRAP
    assert '/etc/sudoers.d/solareclipse-maintenance' in BOOTSTRAP
    assert 'visudo -cf "$SUDOERS_FILE"' in BOOTSTRAP


def test_web_api_explains_when_existing_trigger_needs_bootstrap():
    assert 'release_helper_available' in ROUTES
    assert 'Release updater is not bootstrapped.' in ROUTES
    assert 'install/install_maintenance_helpers.sh' in ROUTES
