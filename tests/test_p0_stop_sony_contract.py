from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_trigger_service_has_no_automatic_30_second_sigkill_timeout():
    text = source("backend/trigger_service.py")
    stop = text[text.index("    def stop(self, rig_id=1, force=False):"):]
    assert "proc.wait(timeout=30)" not in stop
    assert "30 s graceful-stop timeout" not in stop
    assert "if force:" in stop
    assert "proc.kill()" in stop
    assert '"phase": "stopping"' in stop


def test_force_bit_is_carried_end_to_end_and_runtime_shutdown_forces():
    rpc = source("backend/runtime_rpc.py")
    daemon = source("backend/runtime_daemon.py")
    app = source("flask_app/app.py")

    assert '{"rig_id": rig_id, "force": force}' in rpc
    assert 'force = payload.get("force", False)' in daemon
    assert 'kwargs={"rig_id": rig_id, "force": True}' in daemon
    assert 'force = payload.get("force", False)' in app
    assert '_trigger_service.stop(rig_id=rig_id, force=force)' in app


def test_trigger_initialization_log_is_preflight_not_fake_set():
    trigger = source("scripts/eclipse_trigger.py")
    assert trigger.count("Camera initialization/preflight: aperture=") == 2
    assert "SET camera initialize aperture=" not in trigger


def test_profile_reserves_set_label_for_actual_write_paths():
    profile = source("plugins/camera/profile.py")
    assert 'self.log(f"SET camera {key}={target!r}")' in profile
    assert "PREFLIGHT_SETTLE_DELAYS_S = (0.10, 0.25, 0.50, 1.00)" in profile
    assert "READBACK camera {key}={target!r} confirmed after settling" in profile
