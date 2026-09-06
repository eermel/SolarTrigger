"""Thin HTTP adapter; live debug messages deliberately never enter disk logs."""
from flask import jsonify, request
from backend.camera_characterization import JOB
from backend.device_inventory import get_cached_inventory, refresh_inventory


def register_characterization_routes(app, trigger_snapshot):
    @app.before_request
    def characterization_exclusive_access():
        if not JOB.running:
            return None
        path = request.path
        # Characterization owns camera access. Allow polling/confirmation/cancel,
        # but no refresh, rebind, reset or acquisition in another browser tab.
        if path.startswith("/api/camera-characterization"):
            return None
        if (path.startswith("/api/trigger/") and path != "/api/trigger/status"
                or path.startswith("/api/rigs/") and request.method != "GET"
                or "/camera/" in path
                or path.startswith("/api/devices")
                or path.startswith("/api/system/erase")):
            return jsonify(error="Camera characterization owns USB access"), 409

    @app.get("/api/camera-characterization")
    def characterization_status():
        snapshot = JOB.snapshot()
        snapshot["candidates"] = [e for e in get_cached_inventory()["camera"]
                                  if e.get("present") and not e.get("pilotable")]
        return jsonify(snapshot)

    @app.post("/api/camera-characterization/start")
    def characterization_start():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or not isinstance(payload.get("locator"), str):
            return jsonify(error="Camera locator is required"), 400
        with JOB.lock:
            if JOB.running:
                return jsonify(error="Characterization already running"), 409
            state = trigger_snapshot() or {}
            if state.get("running") or any((r or {}).get("running") for r in (state.get("rigs") or {}).values()):
                return jsonify(error="Trigger is running"), 409
            # Resolve the selected physical device server-side; never accept
            # caller-supplied profile paths, model names or arbitrary locators.
            candidates = refresh_inventory()["camera"]
            matches = [e for e in candidates if e.get("transport_locator") == payload.get("locator")
                       and e.get("present") and not e.get("pilotable")]
            if len(matches) != 1:
                return jsonify(error="Unknown or already characterized camera; refresh Devices"), 400
            JOB.start(matches[0])
        return jsonify(status="started"), 202

    @app.post("/api/camera-characterization/answer")
    def characterization_answer():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error="Invalid confirmation"), 400
        try:
            JOB.respond(data.get("question_id"), data.get("answer"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(status="accepted")

    @app.post("/api/camera-characterization/cancel")
    def characterization_cancel():
        with JOB.condition:
            JOB.cancelled = True
            JOB.condition.notify_all()
        return jsonify(status="cancelling")
