from flask import Flask
import pytest
from backend import camera_characterization_routes as routes
from backend.camera_characterization import CharacterizationJob


@pytest.fixture
def api(monkeypatch):
    app = Flask(__name__)
    job = CharacterizationJob()
    monkeypatch.setattr(routes, "JOB", job)
    entry = {"manufacturer": "Test", "model": "Camera", "serial": "1234",
             "transport_locator": "usb:001,002", "present": True, "pilotable": False}
    monkeypatch.setattr(routes, "get_cached_inventory", lambda: {"camera": [entry]})
    routes.register_characterization_routes(app, lambda: {})
    return app.test_client(), job


def test_poll_and_start_resolve_server_identity(api, monkeypatch):
    client, job = api
    selected = []
    monkeypatch.setattr(
        job,
        "start",
        lambda entry, **kwargs: selected.append((entry, kwargs)),
    )
    assert client.get("/api/camera-characterization").get_json()["candidates"][0]["serial"] == "1234"
    assert client.post("/api/camera-characterization/start", json={"locator": "usb:001,002", "model": "Forged"}).status_code == 202
    assert selected[0][0]["model"] == "Camera"
    assert selected[0][1] == {"defer_camera_open": True}
    assert client.post("/api/camera-characterization/start", json={"locator": "usb:999,999"}).status_code == 400


def test_active_job_blocks_trigger_refresh_and_reset(api):
    client, job = api
    job.running = True
    for path in ("/api/trigger/start", "/api/rigs/devices/refresh",
                 "/api/system/erase-persistent-data-and-reboot"):
        assert client.post(path).status_code == 409
    assert client.get("/api/camera-characterization").status_code == 200
    assert client.post("/api/camera-characterization/cancel").status_code == 200
    assert job.cancelled


def test_confirmation_stale_id_rejected(api):
    client, job = api
    job.question = {"id": "current", "message": "Photo?"}
    assert client.post("/api/camera-characterization/answer", json={"question_id": "old", "answer": True}).status_code == 409
    assert client.post("/api/camera-characterization/answer", json={"question_id": "current", "answer": True}).status_code == 200
    assert job.answer is True


@pytest.mark.parametrize("value", [[], ["bad"], "bad", 123])
def test_invalid_start_payload(api, value):
    client, _ = api
    assert client.post("/api/camera-characterization/start", json=value).status_code == 400


def test_recharacterize_starts_full_job_in_replace_mode(api, monkeypatch):
    client, job = api

    entry = {
        "manufacturer": "Test",
        "model": "Camera",
        "serial": "1234",
        "transport_locator": "usb:001,002",
        "present": True,
        "pilotable": True,
    }
    monkeypatch.setattr(
        routes,
        "get_cached_inventory",
        lambda: {"camera": [entry]},
    )

    started = []

    def fake_start(selected, **kwargs):
        started.append((selected, kwargs))

    monkeypatch.setattr(job, "start", fake_start)

    response = client.post(
        "/api/camera-characterization/recharacterize",
        json={"locator": "usb:001,002"},
    )

    assert response.status_code == 202
    assert response.get_json()["mode"] == "recharacterize"
    assert started == [
        (
            entry,
            {
                "replace_existing": True,
                "defer_camera_open": True,
            },
        )
    ]


def test_start_uses_cached_inventory_without_hardware_refresh(api, monkeypatch):
    client, job = api
    entry = {
        "manufacturer": "Cached",
        "model": "Camera",
        "serial": "5678",
        "transport_locator": "usb:005,006",
        "present": True,
        "pilotable": False,
    }
    monkeypatch.setattr(routes, "get_cached_inventory", lambda: {"camera": [entry]})
    selected = []
    monkeypatch.setattr(
        job,
        "start",
        lambda selected_entry, **kwargs: selected.append(
            (selected_entry, kwargs)
        ),
    )

    response = client.post(
        "/api/camera-characterization/start",
        json={"locator": "usb:005,006"},
    )

    assert response.status_code == 202
    assert selected == [
        (entry, {"defer_camera_open": True})
    ]


def test_status_exposes_new_and_characterized_cameras_in_one_qualification_list(api, monkeypatch):
    client, _job = api
    cameras = [
        {"manufacturer":"New","model":"Camera","serial":"N1","transport_locator":"usb:1,1","present":True,"pilotable":False},
        {"manufacturer":"Known","model":"Camera","serial":"K1","transport_locator":"usb:1,2","present":True,"pilotable":True},
    ]
    monkeypatch.setattr(routes, "get_cached_inventory", lambda: {"camera": cameras})
    payload = client.get("/api/camera-characterization").get_json()
    assert [(item["transport_locator"], item["characterized"]) for item in payload["qualification_candidates"]] == [
        ("usb:1,1", False), ("usb:1,2", True)
    ]


def test_characterization_releases_runtime_after_admission_lock(api, monkeypatch):
    client, job = api
    events = []

    class Runtime:
        def release_idle_workers(self):
            events.append("release")

    monkeypatch.setattr(routes, "get_camera_worker_runtime", lambda: Runtime())

    def admit(trigger_busy, start_fn):
        events.append("admission.begin")
        assert trigger_busy() is False
        start_fn()
        events.append("admission.end")

    monkeypatch.setattr(routes, "start_maintenance_if_trigger_idle", admit)
    monkeypatch.setattr(
        job,
        "start",
        lambda entry, **kwargs: events.append(("start", kwargs)),
    )
    monkeypatch.setattr(
        job,
        "release_camera_open",
        lambda: events.append("camera.open") or True,
    )

    response = client.post(
        "/api/camera-characterization/start",
        json={"locator": "usb:001,002"},
    )

    assert response.status_code == 202
    assert events == [
        "admission.begin",
        ("start", {"defer_camera_open": True}),
        "admission.end",
        "release",
        "camera.open",
    ]
