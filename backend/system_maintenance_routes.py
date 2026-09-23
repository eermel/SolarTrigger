from pathlib import Path
import os
import tempfile

from flask import jsonify, request

from backend.runtime_interlock import (
    TriggerActiveError,
    start_maintenance_if_trigger_idle,
)
from backend.system_maintenance import (
    JOB,
    MAX,
    RELEASE_HELPER,
    SYSTEM_HELPER,
    ethernet_status,
    installed_releases,
    internet_available,
    validate_release_version,
    validate_release_zip,
)


def register_system_maintenance_routes(
    app,
    trigger_snapshot,
    trigger_busy=None,
):
    def busy():
        if trigger_busy is not None:
            return bool(trigger_busy())
        snapshot = trigger_snapshot() or {}
        return bool(
            snapshot.get("running")
            or any(
                (rig or {}).get("running")
                for rig in (snapshot.get("rigs") or {}).values()
            )
        )

    def start_job(kind, cmd):
        try:
            start_maintenance_if_trigger_idle(
                busy,
                lambda: JOB.start(kind, cmd),
            )
        except TriggerActiveError:
            return jsonify(error="Trigger is running or starting"), 409
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(status="started"), 202

    @app.get("/api/system/maintenance/status")
    def status():
        ethernet = ethernet_status()
        data = JOB.snapshot()
        data["ethernet"] = ethernet
        data["internet"] = (
            internet_available()
            if ethernet["connected"]
            else False
        )
        data["release_state"] = installed_releases()
        data["release_helper_available"] = Path(RELEASE_HELPER).is_file()
        return jsonify(data)

    @app.get("/api/system/maintenance/releases")
    def releases():
        return jsonify(installed_releases())

    def apt(kind, action):
        if not ethernet_status()["connected"]:
            return jsonify(error="Physical Ethernet link is required"), 409
        if not internet_available():
            return jsonify(
                error="Internet is unavailable through Ethernet"
            ), 409
        return start_job(
            kind,
            ["sudo", "-n", SYSTEM_HELPER, action],
        )

    @app.post("/api/system/maintenance/check-updates")
    def check():
        return apt("apt-check", "check")

    @app.post("/api/system/maintenance/update-system")
    def upgrade():
        return apt("apt-upgrade", "upgrade")

    @app.post("/api/system/maintenance/upload-release")
    def upload():
        if busy() or JOB.snapshot()["running"]:
            return jsonify(error="System is busy"), 409
        if request.content_length is not None and request.content_length > MAX:
            return jsonify(error="Update package is too large"), 413

        uploaded = request.files.get("file")
        if (
            not uploaded
            or not uploaded.filename.lower().endswith(".zip")
        ):
            return jsonify(
                error="Select a SolarTrigger ZIP package"
            ), 400

        fd, name = tempfile.mkstemp(
            prefix="solartrigger-",
            suffix=".zip",
        )
        os.close(fd)
        package = Path(name)

        try:
            uploaded.save(package)
            manifest = validate_release_zip(package)
            destination = package.with_name(
                "solartrigger-"
                + str(manifest["version"])
                + "-"
                + package.name
                + ".zip"
            )
            package.replace(destination)
            return jsonify(
                status="validated",
                version=manifest["version"],
                build_commit=manifest.get("build_commit"),
                file_count=len(manifest.get("files") or {}),
                upload_token=destination.name,
            )
        except ValueError as exc:
            package.unlink(missing_ok=True)
            return jsonify(error=str(exc)), 400

    @app.post("/api/system/maintenance/install-release")
    def install():
        if not Path(RELEASE_HELPER).is_file():
            return jsonify(
                error=(
                    "Release updater is not bootstrapped. Run "
                    "sudo install/install_maintenance_helpers.sh once "
                    "on this existing trigger."
                )
            ), 503

        token = str(
            (request.get_json(silent=True) or {}).get(
                "upload_token"
            )
            or ""
        )
        if not token or Path(token).name != token:
            return jsonify(error="Invalid upload token"), 400

        package = Path("/tmp") / token
        try:
            manifest = validate_release_zip(package)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

        result = start_job(
            "release-install",
            [
                "sudo",
                "-n",
                RELEASE_HELPER,
                "install",
                str(package),
            ],
        )
        if isinstance(result, tuple):
            return result
        response = result.get_json()
        response["version"] = manifest["version"]
        response["reboot"] = True
        return jsonify(response), result.status_code

    @app.post("/api/system/maintenance/rollback-release")
    def rollback():
        if not Path(RELEASE_HELPER).is_file():
            return jsonify(
                error=(
                    "Release updater is not bootstrapped. Run "
                    "sudo install/install_maintenance_helpers.sh once "
                    "on this existing trigger."
                )
            ), 503

        payload = request.get_json(silent=True) or {}
        try:
            version = validate_release_version(
                payload.get("version")
            )
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

        release_state = installed_releases()
        installed = {
            item["version"]: item
            for item in release_state["releases"]
        }
        if version not in installed:
            return jsonify(error="Release is not installed"), 404
        if release_state.get("active") == version:
            return jsonify(error="Release is already active"), 409

        result = start_job(
            "release-rollback",
            [
                "sudo",
                "-n",
                RELEASE_HELPER,
                "rollback",
                version,
            ],
        )
        if isinstance(result, tuple):
            return result
        response = result.get_json()
        response["version"] = version
        response["reboot"] = True
        return jsonify(response), result.status_code
