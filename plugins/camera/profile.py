"""Data-driven camera executor and offline exact-exposure planner."""
from __future__ import annotations

import math
import re
import time

from .base import (
    CameraPlugin,
    CaptureResult,
    _parse_speed,
    seconds_until_deadline,
)
from backend.camera_profiles import validate_profile


def widget(camera, path):
    config = camera.get_config()
    node = config
    parts = path.strip("/").split("/")
    if parts and parts[0] == config.get_name():
        parts.pop(0)
    for part in parts:
        node = node.get_child_by_name(part)
    return config, node


def write_widget(camera, path, value):
    config, node = widget(camera, path)
    # Preserve the widget's native scalar type, notably TOGGLE/RANGE.
    current = node.get_value()
    if isinstance(current, (int, float)):
        value = type(current)(value)
    node.set_value(value)
    camera.set_config(config)


def write_checked(camera, path, value, timeout_s=5.0):
    """Write one setting and verify that the effective value matches."""
    write_widget(camera, path, value)
    deadline = time.monotonic() + timeout_s
    for attempt in range(21):
        _, node = widget(camera, path)
        if str(node.get_value()) == str(value):
            return
        if time.monotonic() >= deadline or attempt == 20:
            raise RuntimeError(f"Setting not effective: {path}={value!r}")
        time.sleep(.05)


def prepare_photo(camera, commands, shutter, mode=None):
    """Legacy/v2 helper kept for characterization compatibility."""
    if "capture_mode" in commands:
        write_checked(
            camera,
            commands["capture_mode"]["path"],
            commands["capture_mode"]["value"],
        )
    write_checked(
        camera,
        commands["shutter"]["path"],
        commands["shutter"]["values"][str(shutter)],
    )
    if mode is not None:
        write_checked(
            camera,
            commands["capture_mode"]["path"],
            mode,
        )


class CameraIdleTimeout(RuntimeError):
    pass


def wait_camera_idle(
    camera,
    observed,
    quiet_s=2.0,
    timeout_s=15.0,
    check=None,
):
    """Drain notifications until a continuous quiet interval after release.

    This is characterization-only. Trigger never calls it.
    """
    import gphoto2 as gp

    started = last_event = time.monotonic()
    while True:
        if check:
            check()
        now = time.monotonic()
        if now - last_event >= quiet_s:
            return (now - started) * 1000
        if now - started >= timeout_s:
            raise CameraIdleTimeout(
                "Camera did not become idle within 15 seconds"
            )
        kind, data = camera.wait_for_event(100)
        if kind != gp.GP_EVENT_TIMEOUT:
            last_event = time.monotonic()
            if kind == gp.GP_EVENT_FILE_ADDED:
                observed.add(
                    (
                        getattr(data, "folder", ""),
                        getattr(data, "name", str(data)),
                    )
                )


class CameraPreflightError(RuntimeError):
    """A required physical camera state cannot be established before START."""

    code = "PREFLIGHT_FAILED"


class ProfilePlugin(CameraPlugin):
    def __init__(self, camera, log_fn=print, profile=None):
        super().__init__(camera, log_fn)
        self.profile = validate_profile(profile)
        self.name = self.profile["backend"]
        self.commands = self.profile["commands"]

    @staticmethod
    def matches(model_string):
        return False  # Factory passes the exact validated model profile.

    _SEMANTIC = {
        "shutterspeed": "shutter",
        "shutterspeed2": "shutter",
        "iso": "iso",
        "capturemode": "capture_mode",
        "f-number": "aperture",
        "aperture": "aperture",
        "manual_mode": "manual_mode",
        "capture_target": "capture_target",
        "raw": "raw",
    }

    def _resolved_value(self, key, value=None):
        spec = self.commands[key]
        if value is None:
            if "value" not in spec:
                raise ValueError(f"No characterized default for {key}")
            return spec["value"]
        values = spec.get("values")
        if values is not None:
            if str(value) not in values:
                raise ValueError(f"Uncharacterized {key} value: {value}")
            return values[str(value)]
        return value

    def _read(self, key):
        spec = self.commands[key]
        _, node = widget(self.camera, spec["path"])
        return node.get_value()

    def _resolve_profile_shutter(self, value):
        """Resolve a requested shutter to one characterized profile spelling.

        Photographically equivalent spellings such as ``1/2`` and ``5/10``
        represent the same exposure duration.  The execution plan may use a
        canonical spelling while gphoto2 exposes another one.  Always return
        the exact characterized profile key that must be sent at runtime.

        Ambiguous or genuinely unsupported durations fail closed.
        """
        requested = str(value)
        values = self.commands["shutter"]["values"]

        if requested in values:
            return requested

        try:
            requested_s = _parse_speed(requested)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise ValueError(
                f"Unsupported profile shutter: {value}"
            ) from exc

        matches = [
            str(candidate)
            for candidate in values
            if math.isclose(
                _parse_speed(str(candidate)),
                requested_s,
                rel_tol=1e-9,
                abs_tol=1e-12,
            )
        ]

        if len(matches) == 1:
            return matches[0]

        raise ValueError(
            f"Unsupported profile shutter: {value}"
        )

    def _live_writable(self, key) -> bool:
        spec = self.commands[key]
        if spec.get("set") is False:
            return False
        _, node = widget(self.camera, spec["path"])
        try:
            return not bool(node.get_readonly())
        except Exception:
            # Legacy profiles did not persist capability flags.  If the live
            # widget cannot report readonly state, preserve historical SET
            # behaviour and let write_checked provide the final proof.
            return spec.get("set") is not False

    def _display_model(self) -> str:
        model = str(self.profile.get("model") or "appareil photo")
        model = re.sub(r"\s*\(PC Control\)\s*$", "", model)
        return model.replace("Alpha-A", "A")

    def _manual_instruction(self, key, target, actual) -> str:
        model = self._display_model()
        if key == "manual_mode" and str(target).casefold() in {"m", "manual"}:
            return f"Mettre le {model} en mode manuel (M)."
        if key == "raw":
            return f"Régler le {model} en RAW (actuel: {actual})."
        if key == "capture_target":
            return (
                f"Régler la destination d'enregistrement du {model} sur "
                f"{target} (actuel: {actual})."
            )
        if (
            key == "capture_mode"
            and str(target).casefold()
            in {"single shot", "single", "single frame"}
        ):
            return (
                f"Mettre le {model} en mode de déclenchement vue par vue "
                f"(Single Shot, actuel: {actual})."
            )

        return (
            f"Régler physiquement {key}={target} sur le {model} "
            f"(actuel: {actual})."
        )

    def _ensure(self, key, value=None) -> bool:
        """GET first; SET only when the required value is different."""
        target = self._resolved_value(key, value)
        actual = self._read(key)
        if str(actual) == str(target):
            return False

        if not self._live_writable(key):
            raise CameraPreflightError(
                self._manual_instruction(key, target, actual)
            )

        try:
            write_checked(self.camera, self.commands[key]["path"], target)
        except Exception as exc:
            # Legacy profiles may lack explicit set=false even when gphoto2
            # exposes a physical-dial setting as superficially writable.  A
            # failed transition at preflight is still an operator-actionable
            # physical requirement, not a generic runtime USB error.
            try:
                actual = self._read(key)
            except Exception as read_exc:
                raise CameraPreflightError(
                    f"Communication avec {self._display_model()} impossible "
                    f"pendant le précontrôle de {key}: {read_exc}"
                ) from exc
            raise CameraPreflightError(
                self._manual_instruction(key, target, actual)
            ) from exc
        return True

    def _apply(self, key, value=None):
        """Apply one characterized SET used by the scheduled runtime."""
        target = self._resolved_value(key, value)
        if not self._live_writable(key):
            actual = self._read(key)
            if str(actual) == str(target):
                return False
            raise CameraPreflightError(
                self._manual_instruction(key, target, actual)
            )

        writer = (
            write_checked
            if self.profile.get("timing_contract")
            else write_widget
        )
        writer(self.camera, self.commands[key]["path"], target)
        return True

    def preflight(self, required_state=None):
        """Validate/configure the body before any timed Trigger command runs.

        Characterized invariants are checked with GET first.  A GET-only
        invariant is accepted when already correct and produces an actionable
        error when a physical control must be changed.  Dynamic state is then
        converged to the effective plan state at the current UTC time.
        """
        required_state = required_state or {}
        if not isinstance(required_state, dict):
            raise ValueError("required_state must be an object")

        changed = []
        for key in (
            "manual_mode",
            "capture_target",
            "raw",
            "capture_mode",
            "self_timer",
            "time_lapse",
        ):
            if key in self.commands and self._ensure(key):
                changed.append(key)

        for parameter, value in required_state.items():
            # capture_setup is the old v2 composite macro.  It is not a stable
            # camera state and therefore is never reconstructed at preflight.
            if parameter == "capture_setup":
                continue
            key = self._SEMANTIC.get(str(parameter))
            if key is None or key not in self.commands:
                raise CameraPreflightError(
                    f"Paramètre requis non caractérisé: {parameter}"
                )
            if self._ensure(key, value):
                changed.append(str(parameter))

        return {
            "ok": True,
            "changed": changed,
            "model": self._display_model(),
        }

    def get_parameter(self, parameter):
        key = self._SEMANTIC.get(str(parameter))
        if key is None or key not in self.commands:
            raise ValueError(f"Uncharacterized parameter: {parameter}")
        return self._read(key)

    def init_settings(
        self,
        aperture=None,
        iso=None,
        image_format="RAW",
        white_balance=None,
    ):
        required = {
            "iso": 100 if iso is None else iso,
        }
        if "capture_mode" in self.commands:
            required["capturemode"] = self.commands["capture_mode"]["value"]
        if aperture is not None and "aperture" in self.commands:
            required["f-number"] = aperture
        return self.preflight(required)

    def set_exposure_settings(self, aperture=None, iso=None):
        if iso is not None:
            self._apply("iso", iso)
        if aperture is not None and "aperture" in self.commands:
            self._apply("aperture", aperture)

    def set_parameter(
        self,
        parameter,
        value,
        fallback_parameter=None,
    ):
        contract = self.profile.get("timing_contract") or {}

        # Contract-v2 compatibility only. Contract v3 emits explicit SET
        # operations instead of the macro capture_setup.
        if (
            parameter == "capture_setup"
            and contract.get("version") == 2
        ):
            if not isinstance(value, dict):
                raise ValueError(
                    "capture_setup requires shutter and optional bracket size"
                )
            frames = int(value.get("frames", 1))
            mode = (
                self.profile["brackets"][str(frames)]["mode"]
                if frames > 1
                else None
            )
            prepare_photo(
                self.camera,
                self.commands,
                str(value["shutter"]),
                mode,
            )
            return True

        key = self._SEMANTIC.get(str(parameter))
        if key is None or key not in self.commands:
            fallback_key = (
                self._SEMANTIC.get(str(fallback_parameter))
                if fallback_parameter is not None
                else None
            )
            if fallback_key is None or fallback_key not in self.commands:
                raise ValueError(f"Uncharacterized parameter: {parameter}")
            key = fallback_key

        self._apply(key, value)
        return True

    def get_battery_level(self):
        if "battery" not in self.commands:
            return None
        try:
            _, node = widget(
                self.camera,
                self.commands["battery"]["path"],
            )
            return int(
                float(str(node.get_value()).rstrip("%"))
            )
        except (ValueError, RuntimeError):
            return None

    def _trigger(self, spec):
        method = spec["method"]
        if method == "trigger_capture":
            self.camera.trigger_capture()
        elif method == "capture":
            import gphoto2 as gp
            return self.camera.capture(gp.GP_CAPTURE_IMAGE)
        elif method == "widget":
            write_widget(
                self.camera,
                spec["path"],
                spec["value"],
            )
        else:
            raise ValueError("unsupported trigger")

    def execute_photo(
        self,
        params,
        *,
        observation_timeout_s=None,
        check=None,
    ):
        """A PHOTO is atomic; release a held shutter even after failure."""
        import gphoto2 as gp

        count = int(params.get("frames", 1))
        views = (
            params.get("physical_views")
            or [params["shutter"]]
        )
        timeout = (
            sum(_parse_speed(value) for value in views)
            + float(self.profile.get("capture_timeout_s", 15))
        )

        # The execution-plan runtime currently uses envelope version 2 for
        # guarded SET/PHOTO admission. Contract v3 deliberately reuses that
        # transport envelope; its duration values are computed by the v3 model.
        guarded = params.get("timing_contract_version") == 2
        if guarded:
            timeout = float(params["duration_ms"]) / 1000.0

        if observation_timeout_s is not None:
            # Characterization can observe beyond a candidate budget, but this
            # override is never serialized into the runtime plan.
            if (
                not math.isfinite(observation_timeout_s)
                or observation_timeout_s <= 0
            ):
                raise ValueError(
                    "invalid characterization observation timeout"
                )
            timeout = max(timeout, observation_timeout_s)

        bracket = count > 1
        spec = (
            self.profile["brackets"][str(count)]["trigger"]
            if bracket
            else self.commands["trigger_single"]
        )

        observed = set()
        deadline = time.monotonic() + timeout

        try:
            # Drain stale notifications from an earlier capture.
            for _ in range(100):
                kind, _ = self.camera.wait_for_event(1)
                if kind == gp.GP_EVENT_TIMEOUT:
                    break

            if check:
                check()

            capture_file = self._trigger(spec)

            if (
                capture_file is not None
                and getattr(capture_file, "name", None)
            ):
                observed.add(
                    (capture_file.folder, capture_file.name)
                )

            if (
                spec.get("completion")
                == "operator_validated_delay"
            ):
                time.sleep(
                    float(spec["wait_ms"]) / 1000.0
                    + sum(_parse_speed(value) for value in views)
                )
                return CaptureResult(
                    frames=count,
                    planned=count,
                    detail=(
                        "operator-validated method; "
                        "frame count not observed"
                    ),
                )

            while (
                time.monotonic() < deadline
                and len(observed) < count
            ):
                if check:
                    check()
                kind, data = self.camera.wait_for_event(100)
                if kind == gp.GP_EVENT_FILE_ADDED:
                    observed.add(
                        (
                            getattr(data, "folder", ""),
                            getattr(data, "name", str(data)),
                        )
                    )
        finally:
            if "release" in spec:
                write_widget(
                    self.camera,
                    spec["path"],
                    spec["release"],
                )

        # Confirm late files after release. There is deliberately no
        # characterization-only quiet period in Trigger.
        if not guarded:
            deadline = (
                time.monotonic()
                + sum(_parse_speed(value) for value in views)
                + 5
            )

        while (
            len(observed) < count
            and time.monotonic() < deadline
        ):
            if check:
                check()
            kind, data = self.camera.wait_for_event(100)
            if kind == gp.GP_EVENT_FILE_ADDED:
                observed.add(
                    (
                        getattr(data, "folder", ""),
                        getattr(data, "name", str(data)),
                    )
                )

        if len(observed) != count:
            raise RuntimeError(
                f"Capture not confirmed: {len(observed)}/{count}"
            )

        return CaptureResult(
            frames=count,
            planned=count,
            detail="profile capture",
        )

    def prepare_capture(self, intent):
        from services.camera_service import PreparedCapture

        plan = intent.exposure_plan

        if plan is None:
            speeds = intent.speeds
            if not speeds:
                lo = _parse_speed(
                    intent.shutter_max or intent.shutter_min
                )
                hi = _parse_speed(
                    intent.shutter_min or intent.shutter_max
                )
                speeds = [
                    value
                    for value in self.commands["shutter"]["values"]
                    if lo <= _parse_speed(value) <= hi
                ]
                speeds.sort(key=_parse_speed)

            # No silently denser sequence when a caller requests 1-EV spacing.
            if not intent.speeds and speeds:
                step = float(intent.step_ev or 1)
                selected = [speeds[0]]
                for speed in speeds[1:]:
                    if (
                        math.log2(
                            _parse_speed(speed)
                            / _parse_speed(selected[-1])
                        )
                        >= step - .12
                    ):
                        selected.append(speed)
                speeds = selected

            plan = [
                {"shutter": str(value), "iso": 100}
                for value in speeds
            ]

        if not plan:
            raise ValueError("empty exposure plan")

        normalized_plan = []

        for exposure in plan:
            if (
                str(exposure["iso"])
                not in self.commands["iso"]["values"]
            ):
                raise ValueError(
                    f"Unsupported profile ISO: {exposure['iso']}"
                )

            normalized_exposure = dict(exposure)
            normalized_exposure["shutter"] = (
                self._resolve_profile_shutter(
                    exposure["shutter"]
                )
            )
            normalized_plan.append(normalized_exposure)

        plan = normalized_plan

        contract = self.profile.get("timing_contract")
        if isinstance(contract, dict):
            if contract.get("version") == 3:
                return self._prepare_budgeted_v3(plan)
            return self._prepare_budgeted_v2(plan)

        # Legacy planner: exact contiguous, ISO-constant 1-EV groups only.
        timings = self.profile.get("planning_timing", {})
        single = timings.get("single_ms", 1)
        costs = [math.inf] * (len(plan) + 1)
        choices = [None] * len(plan)
        costs[-1] = 0

        for index in range(len(plan) - 1, -1, -1):
            costs[index] = single + costs[index + 1]
            choices[index] = 1

            if self.profile["strategy"] != "bracket":
                continue

            for size, spec in self.profile.get(
                "brackets",
                {},
            ).items():
                frames = int(size)
                group = plan[index:index + frames]
                if (
                    len(group) != frames
                    or len({item["iso"] for item in group}) != 1
                ):
                    continue

                group_speeds = [
                    _parse_speed(item["shutter"])
                    for item in group
                ]
                if any(
                    abs(math.log2(second / first) - 1) > 0.12
                    for first, second in zip(
                        group_speeds,
                        group_speeds[1:],
                    )
                ):
                    continue

                cost = spec["total_ms"] + costs[index + frames]
                if cost < costs[index]:
                    costs[index] = cost
                    choices[index] = frames

        operations = []
        index = 0
        last_iso = None

        while index < len(plan):
            frames = choices[index]
            group = plan[index:index + frames]

            if last_iso != group[0]["iso"]:
                operations.append(
                    {
                        "action": "set",
                        "parameter": "iso",
                        "value": str(group[0]["iso"]),
                    }
                )
                last_iso = group[0]["iso"]

            if "capture_mode" in self.commands:
                operations.append(
                    {
                        "action": "set",
                        "parameter": "capturemode",
                        "value": self.commands[
                            "capture_mode"
                        ]["value"],
                    }
                )

            centre = str(group[frames // 2]["shutter"])
            operations.append(
                {
                    "action": "set",
                    "parameter": "shutterspeed",
                    "value": centre,
                }
            )

            if frames > 1:
                operations.append(
                    {
                        "action": "set",
                        "parameter": "capturemode",
                        "value": self.profile[
                            "brackets"
                        ][str(frames)]["mode"],
                    }
                )
                operations.append(
                    {
                        "action": "bracket_press",
                        "centre": centre,
                        "step_ev": 1,
                        "frames": frames,
                        "physical_views": [
                            str(item["shutter"])
                            for item in group
                        ],
                        "duration_ms": (
                            self.profile["brackets"][
                                str(frames)
                            ]["atomic_ms"]
                            + 1000
                            * sum(
                                _parse_speed(item["shutter"])
                                for item in group
                            )
                        ),
                    }
                )
            else:
                operations.append(
                    {
                        "action": "trigger_capture",
                        "shutter": centre,
                        "expected_frames": 1,
                        "duration_ms": (
                            timings.get(
                                "single_atomic_ms",
                                single,
                            )
                            + 1000 * _parse_speed(centre)
                        ),
                    }
                )

            index += frames

        return PreparedCapture(
            token=("profile", operations),
            estimated_total_s=costs[0] / 1000.0,
            exposures_s=[
                _parse_speed(item["shutter"])
                for item in plan
            ],
            planned_count=len(plan),
            plugin_name=self.name,
            materialized=plan,
        )

    def _prepare_budgeted_v2(self, plan):
        """Legacy contract-v2 planner kept unchanged for migration."""
        from services.camera_service import PreparedCapture
        from backend.camera_timing_contract import photo_budget_ms

        contract = self.profile["timing_contract"]

        if (
            contract.get("sustained", {}).get("status")
            != "validated"
        ):
            raise ValueError(
                "Camera has not passed sustained qualification"
            )

        blocks = {
            "1": contract["single"],
            **contract["brackets"],
        }
        costs = [math.inf] * (len(plan) + 1)
        choices = {}
        costs[-1] = 0

        for index in range(len(plan) - 1, -1, -1):
            iso_cost = contract["iso_ms"]

            for size, block in blocks.items():
                frames = int(size)
                group = plan[index:index + frames]

                if (
                    len(group) != frames
                    or len({item["iso"] for item in group}) != 1
                ):
                    continue

                group_speeds = [
                    _parse_speed(item["shutter"])
                    for item in group
                ]

                if (
                    frames > 1
                    and any(
                        abs(math.log2(second / first) - 1) > .12
                        for first, second in zip(
                            group_speeds,
                            group_speeds[1:],
                        )
                    )
                ):
                    continue

                duration = photo_budget_ms(
                    block,
                    sum(group_speeds),
                )
                cost = (
                    iso_cost
                    + block["setup_ms"]
                    + duration
                    + costs[index + frames]
                )

                if cost < costs[index]:
                    costs[index] = cost
                    choices[index] = (
                        frames,
                        duration,
                    )

        operations = []
        index = 0

        while index < len(plan):
            frames, duration = choices[index]
            group = plan[index:index + frames]

            operations.append(
                {
                    "action": "set",
                    "parameter": "iso",
                    "value": str(group[0]["iso"]),
                    "duration_ms": contract["iso_ms"],
                    "timing_contract_version": 2,
                }
            )

            centre = str(group[frames // 2]["shutter"])
            operations.append(
                {
                    "action": "set",
                    "parameter": "capture_setup",
                    "value": {
                        "shutter": centre,
                        "frames": frames,
                    },
                    "duration_ms": blocks[str(frames)][
                        "setup_ms"
                    ],
                    "timing_contract_version": 2,
                }
            )

            operations.append(
                {
                    "action": (
                        "bracket_press"
                        if frames > 1
                        else "trigger_capture"
                    ),
                    "shutter": centre,
                    "centre": centre,
                    "frames": frames,
                    "expected_frames": frames,
                    "physical_views": [
                        str(item["shutter"])
                        for item in group
                    ],
                    "duration_ms": duration,
                    "timing_contract_version": 2,
                }
            )

            index += frames

        return PreparedCapture(
            token=("profile", operations),
            estimated_total_s=costs[0] / 1000.0,
            exposures_s=[
                _parse_speed(item["shutter"])
                for item in plan
            ],
            planned_count=len(plan),
            plugin_name=self.name,
            materialized=plan,
        )

    def _prepare_budgeted_v3(self, plan):
        """Compile the simplified contract-v3 SET + PHOTO model.

        Every physical group is self-contained so a later complete group can
        recover after an earlier USB failure without replaying historical SETs.
        """
        from services.camera_service import PreparedCapture
        from backend.camera_timing_contract import (
            bracket_photo_duration_ms,
            single_photo_duration_ms,
        )

        contract = self.profile["timing_contract"]
        set_ms = float(contract["set_overhead_ms"])

        bracket_sizes = (
            [
                int(value)
                for value in contract.get(
                    "supported_bracket_frames",
                    [],
                )
            ]
            if self.profile["strategy"] == "bracket"
            else []
        )

        choices_by_size = [1, *bracket_sizes]
        costs = [math.inf] * (len(plan) + 1)
        choices = {}
        costs[-1] = 0.0

        has_capture_mode = (
            "capture_mode" in self.commands
            and self.commands["capture_mode"].get("set") is not False
        )

        for index in range(len(plan) - 1, -1, -1):
            for frames in choices_by_size:
                group = plan[index:index + frames]
                if len(group) != frames:
                    continue
                if len({item["iso"] for item in group}) != 1:
                    continue

                group_speeds = [
                    _parse_speed(item["shutter"])
                    for item in group
                ]

                if frames > 1:
                    if str(frames) not in self.profile["brackets"]:
                        continue
                    if any(
                        abs(math.log2(second / first) - 1) > .12
                        for first, second in zip(
                            group_speeds,
                            group_speeds[1:],
                        )
                    ):
                        continue

                    photo_ms = bracket_photo_duration_ms(
                        contract["bracket_overhead_ms"],
                        contract["bracket_inter_image_ms"],
                        group_speeds,
                    )
                else:
                    photo_ms = single_photo_duration_ms(
                        contract["single_overhead_ms"],
                        group_speeds[0],
                    )

                # Self-contained group:
                #   ISO
                #   [Single capture mode]
                #   shutter
                #   [bracket capture mode]
                #   PHOTO
                set_count = 2
                if has_capture_mode:
                    set_count += 1
                if frames > 1:
                    set_count += 1

                total = (
                    set_count * set_ms
                    + photo_ms
                    + costs[index + frames]
                )

                if total < costs[index]:
                    costs[index] = total
                    choices[index] = (
                        frames,
                        photo_ms,
                    )

        if not math.isfinite(costs[0]):
            raise ValueError(
                "No characterized SET/PHOTO grouping covers exposure plan"
            )

        def set_operation(parameter, value):
            return {
                "action": "set",
                "parameter": parameter,
                "value": value,
                "duration_ms": set_ms,
                # Existing Sequencer/Trigger guard envelope. The model that
                # produced duration_ms is contract v3.
                "timing_contract_version": 2,
            }

        operations = []
        index = 0

        while index < len(plan):
            frames, photo_ms = choices[index]
            group = plan[index:index + frames]
            centre = str(group[frames // 2]["shutter"])

            operations.append(
                set_operation(
                    "iso",
                    str(group[0]["iso"]),
                )
            )

            if has_capture_mode:
                operations.append(
                    set_operation(
                        "capturemode",
                        self.commands["capture_mode"]["value"],
                    )
                )

            operations.append(
                set_operation(
                    "shutterspeed",
                    centre,
                )
            )

            if frames > 1:
                operations.append(
                    set_operation(
                        "capturemode",
                        self.profile["brackets"][
                            str(frames)
                        ]["mode"],
                    )
                )

            operations.append(
                {
                    "action": (
                        "bracket_press"
                        if frames > 1
                        else "trigger_capture"
                    ),
                    "shutter": centre,
                    "centre": centre,
                    "frames": frames,
                    "expected_frames": frames,
                    "physical_views": [
                        str(item["shutter"])
                        for item in group
                    ],
                    "duration_ms": photo_ms,
                    "timing_contract_version": 2,
                    "camera_timing_model_version": 3,
                }
            )

            index += frames

        return PreparedCapture(
            token=("profile", operations),
            estimated_total_s=costs[0] / 1000.0,
            exposures_s=[
                _parse_speed(item["shutter"])
                for item in plan
            ],
            planned_count=len(plan),
            plugin_name=self.name,
            materialized=plan,
        )

    def audit_prepared_capture(self, prepared):
        return prepared.token[1]

    def trigger_prepared(self, prepared, deadline=None):
        frames = 0
        for operation in self.audit_prepared_capture(prepared):
            if (
                deadline is not None
                and seconds_until_deadline(deadline) <= 0
            ):
                break
            if operation["action"] == "set":
                self.set_parameter(
                    operation["parameter"],
                    operation["value"],
                )
            else:
                frames += self.execute_photo(operation).frames

        return CaptureResult(
            frames,
            prepared.planned_count,
        )

    def shoot_speeds(
        self,
        v_max,
        v_min,
        step_il,
        photo_num_start=0,
        deadline=None,
    ):
        from services.camera_service import CaptureIntent

        return self.trigger_prepared(
            self.prepare_capture(
                CaptureIntent(
                    shutter_max=v_max,
                    shutter_min=v_min,
                    step_ev=step_il,
                    speeds=None,
                    phase="manual",
                    target_time=None,
                    deadline=deadline,
                    overflow_policy="truncate",
                )
            ),
            deadline,
        )
