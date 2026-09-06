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
    monkeypatch.setattr(routes, "refresh_inventory", lambda: {"camera": [entry]})
    routes.register_characterization_routes(app, lambda: {})
    return app.test_client(), job


def test_poll_and_start_resolve_server_identity(api, monkeypatch):
    client, job = api
    selected = []
    monkeypatch.setattr(job, "start", lambda entry: selected.append(entry))
    assert client.get("/api/camera-characterization").get_json()["candidates"][0]["serial"] == "1234"
    assert client.post("/api/camera-characterization/start", json={"locator": "usb:001,002", "model": "Forged"}).status_code == 202
    assert selected[0]["model"] == "Camera"
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
