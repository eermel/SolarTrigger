from types import SimpleNamespace

import pytest

from plugins.camera import profile as profile_module
from backend.camera_ipc_server import CameraIpcServer, IpcError


def test_camera_ipc_allows_disjoint_rig_sessions_and_keeps_them_isolated(tmp_path):
    class Runtime:
        def __init__(self):
            self.workers = {1: object(), 2: object()}
        def active_camera_rig_ids(self):
            return (1, 2)
        def get_for_rig(self, rig_id):
            return self.workers.get(rig_id)

    server = CameraIpcServer(
        Runtime(), endpoint_dir=tmp_path / "ipc", parent_pid=4321,
        log_fn=lambda _line: None,
    )
    session1 = server.activate_session("rig-1-session", (1,))
    session2 = server.activate_session("rig-2-session", (2,))

    assert server.handle_request({
        "operation": "list_active_camera_rigs", "params": {},
        "session_id": session1,
    }) == {"rig_ids": [1]}
    assert server.handle_request({
        "operation": "list_active_camera_rigs", "params": {},
        "session_id": session2,
    }) == {"rig_ids": [2]}

    server.revoke_session(session1)
    assert server.handle_request({
        "operation": "list_active_camera_rigs", "params": {},
        "session_id": session2,
    }) == {"rig_ids": [2]}


def test_camera_ipc_rejects_overlapping_rig_leases(tmp_path):
    class Runtime:
        def active_camera_rig_ids(self):
            return (1, 2)
        def get_for_rig(self, _rig_id):
            return object()

    server = CameraIpcServer(
        Runtime(), endpoint_dir=tmp_path / "ipc", parent_pid=4321,
        log_fn=lambda _line: None,
    )
    server.activate_session("first", (1,))
    with pytest.raises(IpcError) as caught:
        server.activate_session("second", (1, 2))
    assert caught.value.code == "SESSION_ACTIVE"

def _profile(commands):
    return {
        "schema_version": 1,
        "config_type": "camera_profile",
        "backend": "profile-test_camera",
        "manufacturer": "Sony",
        "model": "Sony Alpha-A6600 (PC Control)",
        "strategy": "sequential",
        "commands": {
            **commands,
            "capture_target": {
                "path": "/capturetarget", "value": "card+sdram",
                "get": True, "set": True,
            },
            "raw": {
                "path": "/raw", "value": "RAW",
                "get": True, "set": True,
            },
            "iso": {
                "path": "/iso", "value": "100",
                "values": {"100": "100", "200": "200"},
                "get": True, "set": True,
            },
            "shutter": {
                "path": "/shutter", "value": "1/500",
                "values": {"1/500": "1/500", "1/250": "1/250"},
                "get": True, "set": True,
            },
            "trigger_single": {"method": "trigger_capture"},
        },
        "warnings": [],
        "brackets": {},
    }


def test_get_only_manual_mode_mismatch_has_actionable_a6600_message(monkeypatch):
    profile = _profile({
        "manual_mode": {
            "path": "/manual", "value": "M",
            "get": True, "set": False,
        },
    })
    plugin = profile_module.ProfilePlugin(object(), profile=profile)

    nodes = {
        "/manual": SimpleNamespace(
            get_value=lambda: "A", get_readonly=lambda: 1
        ),
        "/capturetarget": SimpleNamespace(
            get_value=lambda: "card+sdram", get_readonly=lambda: 0
        ),
        "/raw": SimpleNamespace(
            get_value=lambda: "RAW", get_readonly=lambda: 0
        ),
        "/iso": SimpleNamespace(
            get_value=lambda: "100", get_readonly=lambda: 0
        ),
        "/shutter": SimpleNamespace(
            get_value=lambda: "1/500", get_readonly=lambda: 0
        ),
    }
    monkeypatch.setattr(
        profile_module,
        "widget",
        lambda _camera, path: (None, nodes[path]),
    )

    with pytest.raises(profile_module.CameraPreflightError) as error:
        plugin.preflight({"iso": "100"})

    assert "Sony A6600" in str(error.value)
    assert "manual mode (M)" in str(error.value)


def test_preflight_get_first_does_not_resend_equal_iso(monkeypatch):
    profile = _profile({
        "manual_mode": {
            "path": "/manual", "value": "M",
            "get": True, "set": False,
        },
    })
    plugin = profile_module.ProfilePlugin(object(), profile=profile)

    values = {
        "/manual": "M",
        "/capturetarget": "card+sdram",
        "/raw": "RAW",
        "/iso": "100",
        "/shutter": "1/500",
    }
    nodes = {
        path: SimpleNamespace(
            get_value=(lambda p=path: values[p]),
            get_readonly=(lambda p=path: 1 if p == "/manual" else 0),
        )
        for path in values
    }
    writes = []
    monkeypatch.setattr(
        profile_module,
        "widget",
        lambda _camera, path: (None, nodes[path]),
    )
    monkeypatch.setattr(
        profile_module,
        "write_checked",
        lambda _camera, path, value: writes.append((path, value)),
    )

    plugin.preflight({"iso": "100"})
    assert writes == []


