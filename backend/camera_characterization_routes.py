"""Thin HTTP adapter; live debug messages deliberately never enter disk logs."""
from flask import jsonify, request
from backend.camera_characterization import JOB
from backend.camera_worker_runtime import get_camera_worker_runtime
from backend.device_inventory import get_cached_inventory, reclassify_cached_cameras
from backend.runtime_interlock import TriggerActiveError, start_maintenance_if_trigger_idle


def _trigger_running(snapshot) -> bool:
    state = snapshot() or {}
    if state.get("running"):
        return True
    rigs = state.get("rigs") or {}
    return (
        isinstance(rigs, dict)
        and any(
            isinstance(value, dict) and value.get("running")
            for value in rigs.values()
        )
    )


def register_characterization_routes(app, trigger_snapshot, emit_fn=None):
    reclassified_job_id = None

    def status_snapshot():
        nonlocal reclassified_job_id
        snapshot = JOB.snapshot()
        reclassified_now = False
        result = snapshot.get("result")
        job_id = snapshot.get("job_id")
        if (
            not snapshot.get("running")
            and job_id
            and job_id != reclassified_job_id
            and isinstance(result, dict)
            and result.get("status") in {"SUCCESS", "PARTIAL"}
        ):
            reclassify_cached_cameras()
            reclassified_job_id = job_id
            reclassified_now = True

        cameras = get_cached_inventory()["camera"]
        snapshot["candidates"] = [
            entry
            for entry in cameras
            if entry.get("present") and not entry.get("pilotable")
        ]
        snapshot["recharacterization_candidates"] = [
            entry
            for entry in cameras
            if entry.get("present") and entry.get("pilotable")
        ]
        snapshot["inventory_reclassified"] = reclassified_now
        return snapshot

    def notify_status():
        if callable(emit_fn):
            emit_fn("camera_characterization_status", status_snapshot())

    JOB.set_notify_fn(notify_status)
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
        return jsonify(status_snapshot())

    @app.post("/api/camera-characterization/start")
    def characterization_start():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or not isinstance(payload.get("locator"), str):
            return jsonify(error="Camera locator is required"), 400
        with JOB.lock:
            if JOB.running:
                return jsonify(error="Characterization already running"), 409
            # Resolve the selected physical device server-side; never accept
            # caller-supplied profile paths, model names or arbitrary locators.
            candidates = get_cached_inventory()["camera"]
            matches = [e for e in candidates if e.get("transport_locator") == payload.get("locator")
                       and e.get("present") and not e.get("pilotable")]
            if len(matches) != 1:
                return jsonify(error="Unknown or already characterized camera; refresh Devices"), 400

            def admit_characterization():
                runtime = get_camera_worker_runtime()
                runtime.release_idle_workers()
                JOB.start(matches[0])

            try:
                start_maintenance_if_trigger_idle(
                    lambda: _trigger_running(trigger_snapshot),
                    admit_characterization,
                )
            except TriggerActiveError:
                return jsonify(error="Trigger is running"), 409
            except RuntimeError as exc:
                return jsonify(error=str(exc)), 409
        return jsonify(status="started"), 202

    @app.post("/api/camera-characterization/recharacterize")
    def characterization_recharacterize():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or not isinstance(payload.get("locator"), str):
            return jsonify(error="Camera locator is required"), 400
        with JOB.lock:
            if JOB.running:
                return jsonify(error="Characterization already running"), 409
            matches = [e for e in get_cached_inventory()["camera"] if e.get("transport_locator") == payload["locator"] and e.get("present") and e.get("pilotable")]
            if len(matches) != 1:
                return jsonify(error="Unknown or uncharacterized camera; refresh Devices"), 400

            def admit_recharacterization():
                runtime = get_camera_worker_runtime()
                runtime.release_idle_workers()
                JOB.start(
                    matches[0],
                    replace_existing=True,
                )

            try:
                start_maintenance_if_trigger_idle(
                    lambda: _trigger_running(trigger_snapshot),
                    admit_recharacterization,
                )
            except TriggerActiveError:
                return jsonify(error="Trigger is running"), 409
            except RuntimeError as exc:
                return jsonify(error=str(exc)), 409
        return jsonify(status="started", mode="recharacterize"), 202

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
        JOB.cancel()
        return jsonify(status="cancelling")
