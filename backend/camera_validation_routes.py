"""HTTP adapter for end-to-end camera validation."""
from __future__ import annotations

from pathlib import Path

from flask import jsonify, request

from backend.camera_characterization import JOB as CHARACTERIZATION_JOB
from backend.camera_validation import (
    CameraValidationError,
    JOB,
    ROOT,
    validation_candidates,
)
from backend.device_inventory import get_cached_inventory, refresh_inventory


def _trigger_is_running(snapshot) -> bool:
    state = snapshot() or {}
    if state.get("running"):
        return True
    rigs = state.get("rigs") or {}
    if isinstance(rigs, dict):
        return any(isinstance(value, dict) and value.get("running") for value in rigs.values())
    return False


def register_camera_validation_routes(app, trigger_snapshot, root=ROOT):
    root = Path(root)

    @app.before_request
    def camera_validation_exclusive_access():
        if not JOB.running:
            return None
        path = request.path
        if path.startswith("/api/camera-validation"):
            return None
        if (
            path.startswith("/api/camera-characterization")
            or (path.startswith("/api/trigger/") and path != "/api/trigger/status")
            or (path.startswith("/api/rigs/") and request.method != "GET")
            or "/camera/" in path
            or path.startswith("/api/devices")
            or path.startswith("/api/system/erase")
        ):
            return jsonify(error="Camera validation owns USB access"), 409
        return None

    @app.get("/api/camera-validation")
    def camera_validation_status():
        snapshot = JOB.snapshot()
        candidates, rejected = validation_candidates(get_cached_inventory(), root)
        snapshot["candidates"] = candidates
        snapshot["rejected_candidates"] = rejected
        return jsonify(snapshot)

    @app.post("/api/camera-validation/prepare")
    def camera_validation_prepare():
        payload = request.get_json(silent=True) or {}
        locator = payload.get("locator") if isinstance(payload, dict) else None
        if not isinstance(locator, str) or not locator.strip():
            return jsonify(error="Camera locator is required"), 400

        if JOB.running:
            return jsonify(error="Camera validation already running"), 409
        if CHARACTERIZATION_JOB.running:
            return jsonify(error="Camera characterization is running"), 409
        if _trigger_is_running(trigger_snapshot):
            return jsonify(error="Trigger is running"), 409

        inventory = refresh_inventory()
        matches = [
            entry
            for entry in inventory.get("camera", [])
            if isinstance(entry, dict)
            and entry.get("present")
            and entry.get("pilotable")
            and entry.get("transport_locator") == locator
        ]
        if len(matches) != 1:
            return jsonify(error="Unknown or unavailable characterized camera; refresh Devices"), 400

        try:
            prepared = JOB.prepare(matches[0], root)
        except CameraValidationError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            return jsonify(error=f"Validation preparation failed: {exc}"), 500

        return jsonify(
            status="prepared",
            warning=(
                "REAL CAMERA VALIDATION: the camera settings will change and real RAW photos "
                "will be written to the card. Review the estimated photo count and duration "
                "before authorizing START."
            ),
            **prepared,
        )

    @app.post("/api/camera-validation/start")
    def camera_validation_start():
        payload = request.get_json(silent=True) or {}
        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            return jsonify(error="Validation authorization token is required"), 400
        if CHARACTERIZATION_JOB.running:
            return jsonify(error="Camera characterization is running"), 409
        if _trigger_is_running(trigger_snapshot):
            return jsonify(error="Trigger is running"), 409
        try:
            JOB.start(token, root)
        except CameraValidationError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(status="started"), 202

    @app.post("/api/camera-validation/answer")
    def camera_validation_answer():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify(error="Invalid confirmation"), 400
        try:
            JOB.respond(payload.get("question_id"), payload.get("outcome"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(status="accepted")

    @app.post("/api/camera-validation/cancel")
    def camera_validation_cancel():
        JOB.cancel()
        return jsonify(status="cancelling")

    @app.post("/api/camera-validation/delete-files")
    def camera_validation_delete_files():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or payload.get("confirm") is not True:
            return jsonify(error="Explicit deletion confirmation is required"), 400
        try:
            audit = JOB.delete_generated_files(root)
            refresh_inventory()
        except CameraValidationError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(status="deleted", **audit)


__all__ = ["register_camera_validation_routes"]
