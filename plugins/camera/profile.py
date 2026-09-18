"""Data-driven camera executor and offline exact-exposure planner."""
from __future__ import annotations

import math
import re
import time
from types import SimpleNamespace

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
    """Legacy full-tree writer.

    This performs a camera.get_config() and therefore is forbidden on the
    characterized timed path.  It remains for legacy profiles and non-timed
    compatibility code only.
    """
    config, node = widget(camera, path)
    # Preserve the widget's native scalar type, notably TOGGLE/RANGE.
    current = node.get_value()
    if isinstance(current, (int, float)):
        value = type(current)(value)
    node.set_value(value)
    camera.set_config(config)


def prime_single_config(camera, spec):
    """Fetch one characterized widget before timed execution.

    The returned CameraWidget is an in-memory template.  Runtime SETs mutate
    this template and call set_single_config(); they never call get_config().
    """
    name = spec.get("name")
    if not isinstance(name, str) or not name:
        raise RuntimeError("characterized single-config writer has no name")
    getter = getattr(camera, "get_single_config", None)
    if not callable(getter):
        raise RuntimeError("gphoto2 get_single_config is unavailable")
    return getter(name)


def write_single_config(camera, spec, node, value):
    """Perform one application-level single-config SET using a pre-fetched widget."""
    name = spec.get("name")
    if not isinstance(name, str) or not name:
        raise RuntimeError("characterized single-config writer has no name")
    setter = getattr(camera, "set_single_config", None)
    if not callable(setter):
        raise RuntimeError("gphoto2 set_single_config is unavailable")

    # get_value() reads the already cached CameraWidget object; it is not a
    # camera/USB GET.  It is used only to preserve native scalar types.
    current = node.get_value()
    if isinstance(current, (int, float)):
        value = type(current)(value)
    node.set_value(value)
    setter(name, node)


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
    """Camera preflight failed before START."""

    code = "PREFLIGHT_FAILED"


class CameraPhysicalPreflightError(CameraPreflightError):
    """A required camera state can only be corrected physically."""

    code = "PREFLIGHT_PHYSICAL_ACTION_REQUIRED"


class ProfilePlugin(CameraPlugin):
    # Persistent worker instances own the effective camera-state cache.
    stateful_settings = True

    def __init__(self, camera, log_fn=print, profile=None):
        super().__init__(camera, log_fn)
        self.profile = validate_profile(profile)
        self.name = self.profile["backend"]
        self.commands = self.profile["commands"]
        # All access to a profile camera is serialized by its rig worker.
        # Remember only settings that this instance has read or written
        # successfully.  A failed capture invalidates this knowledge so the
        # next physical group replays its complete characterized preamble.
        self._known_settings = {}
        # Positive-only cache of settings proven writable at least once.
        # Never cache a live read-only result: on Sony, writability can be
        # transient or mode-dependent. A successful SET is sufficient proof
        # to skip the dedicated readonly GET on later writes; if a write later
        # fails, that key is evicted so the next attempt probes again.
        self._writable_cache = set()
        # Characterized set_single_config widgets are fetched during preflight
        # only.  Once START/timed execution begins, this cache is the sole
        # source used by direct SETs: no configuration GET is permitted.
        self._single_config_widgets = {}

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
        "image_format": "raw",
        "whitebalance": "white_balance",
        "white_balance": "white_balance",
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
        if spec.get("writer") == "single_config":
            node = prime_single_config(self.camera, spec)
            self._single_config_widgets[spec["name"]] = node
            return node.get_value()
        _, node = widget(self.camera, spec["path"])
        return node.get_value()

    def _prime_single_spec(self, spec):
        if spec.get("writer") != "single_config":
            return
        name = spec.get("name")
        if name not in self._single_config_widgets:
            self._single_config_widgets[name] = prime_single_config(
                self.camera, spec
            )

    def _prime_runtime_writers(self):
        """Prime all direct writers before the timed trigger path starts."""
        for spec in self.commands.values():
            if isinstance(spec, dict) and spec.get("set") is not False:
                self._prime_single_spec(spec)

        trigger_specs = [self.commands.get("trigger_single")]
        trigger_specs.extend(
            bracket.get("trigger")
            for bracket in self.profile.get("brackets", {}).values()
            if isinstance(bracket, dict)
        )
        for spec in trigger_specs:
            if isinstance(spec, dict) and spec.get("method") == "widget":
                self._prime_single_spec(spec)

    def _direct_set_spec(self, spec, value):
        """SET from the primed cache; deliberately never performs a GET."""
        if spec.get("writer") != "single_config":
            raise RuntimeError("command is not characterized for direct SET")
        name = spec.get("name")
        node = self._single_config_widgets.get(name)
        if node is None:
            raise CameraPreflightError(
                f"Direct SET writer {name!r} was not primed before START"
            )
        write_single_config(self.camera, spec, node, value)

    def _invalidate_after_set(self, key):
        spec = self.commands.get(key, {})
        for invalidated in spec.get("invalidates", []):
            self._known_settings.pop(str(invalidated), None)

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
        if key in self._writable_cache:
            return True

        _, node = widget(self.camera, spec["path"])
        try:
            writable = not bool(node.get_readonly())
        except Exception:
            # Legacy profiles did not persist capability flags.  If the live
            # widget cannot report readonly state, preserve historical SET
            # behaviour. Do not cache this optimistic result: only an
            # observed writable widget or a successful SET is durable proof.
            return True

        if writable:
            self._writable_cache.add(key)
        return writable

    def _display_model(self) -> str:
        model = str(self.profile.get("model") or "camera")
        model = re.sub(r"\s*\(PC Control\)\s*$", "", model)
        return model.replace("Alpha-A", "A")

    def preparation_lead_s(self) -> float:
        """Conservative SET-preparation reservation before PHOTO target.

        New v3 characterizations publish a directly measured prepare_lead_ms.
        Existing profiles remain usable: their historical total_ms/atomic_ms
        split already measured the SET preparation preceding the atomic PHOTO.
        """
        contract = self.profile.get("timing_contract")
        if isinstance(contract, dict) and contract.get("version") == 3:
            measured = contract.get("prepare_lead_ms")
            if (
                type(measured) in (int, float)
                and math.isfinite(float(measured))
                and float(measured) >= 0
            ):
                return float(measured) / 1000.0

            # v3 compatibility for profiles characterized before prepare_lead_ms
            # existed. Reserve the complete worst-case SET preamble.
            set_ms = float(contract.get("set_overhead_ms", 0) or 0)
            set_count = 4 if self.profile.get("brackets") else 3
            return max(0.0, set_count * set_ms / 1000.0)

        leads_ms = []
        planning = self.profile.get("planning_timing", {})
        single_total = planning.get("single_ms")
        single_atomic = planning.get("single_atomic_ms")
        if (
            type(single_total) in (int, float)
            and type(single_atomic) in (int, float)
        ):
            leads_ms.append(max(0.0, float(single_total) - float(single_atomic)))

        for spec in self.profile.get("brackets", {}).values():
            total = spec.get("total_ms")
            atomic = spec.get("atomic_ms")
            if type(total) in (int, float) and type(atomic) in (int, float):
                leads_ms.append(max(0.0, float(total) - float(atomic)))

        return max(leads_ms, default=0.0) / 1000.0

    def _manual_instruction(self, key, target, actual) -> str:
        model = self._display_model()
        if key == "manual_mode" and str(target).casefold() in {"m", "manual"}:
            return f"Set the {model} to manual mode (M)."
        if key == "raw":
            return f"Set the {model} to RAW (current: {actual})."
        if key == "capture_target":
            return (
                f"Set the recording destination of the {model} to "
                f"{target} (current: {actual})."
            )
        if (
            key == "capture_mode"
            and str(target).casefold()
            in {"single shot", "single", "single frame"}
        ):
            return (
                f"Set the {model} in single-shot release mode "
                f"(Single Shot, current: {actual})."
            )

        return (
            f"Physically set {key}={target} on the {model} "
            f"(current: {actual})."
        )

    def _ensure(self, key, value=None) -> bool:
        """Preflight GET first; SET only when the required value differs."""
        spec = self.commands[key]
        target = self._resolved_value(key, value)
        actual = self._read(key)
        if str(actual) == str(target):
            self._known_settings[key] = target
            return False

        # A non-controllable aperture is a valid manual lens/telescope.  Its
        # f-number is informational and must never block camera initialization.
        if key == "aperture" and spec.get("set") is False:
            self._known_settings.pop(key, None)
            return False

        if spec.get("writer") == "single_config":
            if spec.get("set") is False:
                raise CameraPhysicalPreflightError(
                    self._manual_instruction(key, target, actual)
                )
            try:
                self._direct_set_spec(spec, target)
                verified = self._read(key)
                if str(verified) != str(target):
                    raise RuntimeError(
                        f"readback mismatch: requested={target!r}, "
                        f"actual={verified!r}"
                    )
            except Exception as exc:
                try:
                    actual = self._read(key)
                except Exception as read_exc:
                    raise CameraPreflightError(
                        f"Communication with {self._display_model()} failed "
                        f"during preflight of {key}: {read_exc}"
                    ) from exc
                raise CameraPreflightError(
                    f"USB preflight SET failed for {key} on "
                    f"{self._display_model()}: requested={target!r}, "
                    f"current={actual!r}: {exc}"
                ) from exc
            self._invalidate_after_set(key)
            self._known_settings[key] = target
            return True

        if not self._live_writable(key):
            if spec.get("set") is False:
                raise CameraPhysicalPreflightError(
                    self._manual_instruction(key, target, actual)
                )
            raise CameraPreflightError(
                f"Characterized writable setting {key} is currently read-only "
                f"on {self._display_model()}: requested={target!r}, "
                f"current={actual!r}"
            )

        try:
            write_checked(self.camera, spec["path"], target)
        except Exception as exc:
            self._writable_cache.discard(key)
            try:
                actual = self._read(key)
            except Exception as read_exc:
                raise CameraPreflightError(
                    f"Communication with {self._display_model()} failed "
                    f"during preflight of {key}: {read_exc}"
                ) from exc
            raise CameraPreflightError(
                f"USB preflight SET failed for {key} on "
                f"{self._display_model()}: requested={target!r}, "
                f"current={actual!r}: {exc}"
            ) from exc
        self._invalidate_after_set(key)
        self._known_settings[key] = target
        self._writable_cache.add(key)
        return True

    def _apply(self, key, value=None):
        """Timed SET path: no camera GET, and no SET when state is unchanged."""
        spec = self.commands[key]
        target = self._resolved_value(key, value)
        if (
            key in self._known_settings
            and str(self._known_settings[key]) == str(target)
        ):
            return False

        # Manual optics are intentionally ignored.  The profile may retain a
        # readable aperture for diagnostics, but runtime never tries to set it.
        if key == "aperture" and spec.get("set") is False:
            return False

        if spec.get("writer") == "single_config":
            if spec.get("set") is False:
                raise CameraPreflightError(
                    f"Characterized setting {key} is not writable"
                )
            self._direct_set_spec(spec, target)
            self._invalidate_after_set(key)
            self._known_settings[key] = target
            return True

        # Legacy profile compatibility. New characterizations never use this
        # branch for timed ISO/shutter/capture-mode SETs because it performs a
        # configuration GET before SET.
        if not self._live_writable(key):
            actual = self._read(key)
            if str(actual) == str(target):
                self._known_settings[key] = target
                return False
            raise CameraPreflightError(
                self._manual_instruction(key, target, actual)
            )
        try:
            write_widget(self.camera, spec["path"], target)
        except Exception:
            self._writable_cache.discard(key)
            raise
        self._invalidate_after_set(key)
        self._known_settings[key] = target
        self._writable_cache.add(key)
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
            "shutter_mode",
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
                    f"Required parameter is not characterized: {parameter}"
                )
            if key == "aperture" and self.commands[key].get("set") is False:
                # Manual lens / telescope: aperture is not a controllable camera
                # variable and therefore is never a preflight failure.
                continue
            if self._ensure(key, value):
                changed.append(str(parameter))

        # Prime every direct writer now, while camera GETs are still allowed.
        # The timed Trigger path treats a missing primed widget as an error
        # rather than silently issuing a USB GET.
        self._prime_runtime_writers()

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
        if str(image_format or "RAW").strip().casefold() not in {
            "raw", "nef", "arw", "cr2", "cr3", "orf", "raf", "rw2"
        }:
            raise CameraPreflightError(
                "Only the characterized RAW acquisition path is supported"
            )
        required = {"iso": 100 if iso is None else iso}
        if "capture_mode" in self.commands:
            required["capturemode"] = self.commands["capture_mode"]["value"]
        if (
            aperture is not None
            and "aperture" in self.commands
            and self.commands["aperture"].get("set") is not False
        ):
            required["f-number"] = aperture

        # image_format is a generic acquisition intent. Translate it to the
        # exact physical RAW spelling characterized for this camera profile.
        #
        # Examples:
        #   Sony ILCE-7M5 -> RAW
        #   Nikon D850     -> NEF (Raw)
        #
        # Keep the semantic "image_format" key so preflight() uses the normal
        # raw mapping while never sending the generic API spelling blindly.
        if image_format is not None and "raw" in self.commands:
            required["image_format"] = self.commands["raw"]["value"]

        if white_balance is not None and "white_balance" in self.commands:
            required["white_balance"] = white_balance
        return self.preflight(required)

    def set_exposure_settings(self, aperture=None, iso=None):
        changed = False
        if iso is not None:
            changed = self._apply("iso", iso) or changed
        if (
            aperture is not None
            and "aperture" in self.commands
            and self.commands["aperture"].get("set") is not False
        ):
            changed = self._apply("aperture", aperture) or changed
        return changed

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
            if spec.get("writer") == "single_config":
                self._direct_set_spec(spec, spec["value"])
            else:
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
                if spec.get("writer") == "single_config":
                    self._direct_set_spec(spec, spec["release"])
                else:
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

        # VALIDATION-ONLY LATE CONFIRMATION V2
        # The production Trigger remains fail-closed at the characterized
        # budget. Camera Validation can keep observing FILE_ADDED briefly after
        # that budget to distinguish "late USB confirmation" from a truly
        # missing physical photo. The overrun is still measured and reported.
        late_confirmation = False
        validation_grace_ms = params.get("validation_confirmation_grace_ms", 0)
        if (
            isinstance(validation_grace_ms, bool)
            or not isinstance(validation_grace_ms, (int, float))
            or not math.isfinite(float(validation_grace_ms))
            or float(validation_grace_ms) < 0
        ):
            raise ValueError("invalid validation_confirmation_grace_ms")
        if (
            guarded
            and len(observed) < count
            and float(validation_grace_ms) > 0
        ):
            grace_deadline = (
                time.monotonic()
                + float(validation_grace_ms) / 1000.0
            )
            while (
                len(observed) < count
                and time.monotonic() < grace_deadline
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
            late_confirmation = len(observed) == count

        if len(observed) != count:
            error = RuntimeError(
                f"Capture not confirmed: {len(observed)}/{count}"
            )
            # Preserve the machine-observed count across the worker/IPC
            # boundary so validation can report X/N instead of a generic
            # INTERNAL_ERROR. Runtime behaviour remains fail-closed.
            error.observed_frames = len(observed)
            error.expected_frames = count
            raise error

        return CaptureResult(
            frames=count,
            planned=count,
            detail=(
                "profile capture; validation confirmation after budget"
                if late_confirmation
                else "profile capture"
            ),
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

            iso_values = self.commands["iso"]["values"]
            known_iso = self._known_settings.get("iso")
            logical_iso = next(
                (
                    str(candidate)
                    for candidate, physical in iso_values.items()
                    if known_iso is not None
                    and str(physical) == str(known_iso)
                ),
                "100",
            )
            plan = [
                {"shutter": str(value), "iso": logical_iso}
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

        # Explicit execution-group markers are physical boundaries, not
        # merely metadata. In particular, an auxiliary Atmos exposure must
        # remain one single PHOTO and must never be absorbed into or reshape
        # the user's configured native bracket.
        segments = []
        current = []

        for exposure in plan:
            if (
                current
                and exposure.get("sequence_group")
                != current[-1].get("sequence_group")
            ):
                segments.append(current)
                current = []
            current.append(exposure)

        if current:
            segments.append(current)

        if len(segments) > 1:
            prepared_segments = [
                self.prepare_capture(
                    SimpleNamespace(exposure_plan=segment)
                )
                for segment in segments
            ]
            return PreparedCapture(
                token=(
                    "profile",
                    [
                        operation
                        for prepared in prepared_segments
                        for operation in prepared.token[1]
                    ],
                ),
                estimated_total_s=sum(
                    float(prepared.estimated_total_s or 0.0)
                    for prepared in prepared_segments
                ),
                exposures_s=[
                    exposure_s
                    for prepared in prepared_segments
                    for exposure_s in prepared.exposures_s
                ],
                planned_count=sum(
                    int(prepared.planned_count or 0)
                    for prepared in prepared_segments
                ),
                plugin_name=self.name,
                materialized=plan,
            )

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

                # Conservative planner cost. Runtime later removes
                # every SET whose value is already known to be effective.
                # Native brackets characterized with in-bracket shutter SET
                # need only one capture-mode SET instead of the historical
                # Single->shutter->Bracket round-trip.
                set_count = 2  # ISO + shutter
                if has_capture_mode:
                    if frames > 1:
                        bracket_spec = self.profile["brackets"][str(frames)]
                        set_count += (
                            2
                            if bracket_spec.get(
                                "shutter_requires_single_mode", True
                            )
                            else 1
                        )
                    else:
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

            if frames > 1:
                bracket_spec = self.profile["brackets"][str(frames)]
                requires_single = bracket_spec.get(
                    "shutter_requires_single_mode", True
                )
                if has_capture_mode and requires_single:
                    operations.append(
                        set_operation(
                            "capturemode",
                            self.commands["capture_mode"]["value"],
                        )
                    )
                    operations.append(
                        set_operation("shutterspeed", centre)
                    )
                    operations.append(
                        set_operation("capturemode", bracket_spec["mode"])
                    )
                else:
                    if has_capture_mode:
                        operations.append(
                            set_operation("capturemode", bracket_spec["mode"])
                        )
                    operations.append(
                        set_operation("shutterspeed", centre)
                    )
            else:
                if has_capture_mode:
                    operations.append(
                        set_operation(
                            "capturemode",
                            self.commands["capture_mode"]["value"],
                        )
                    )
                operations.append(
                    set_operation("shutterspeed", centre)
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

    def sync_datetime(self, ref):
        from backend.camera_auxiliary_capabilities import sync_profile_datetime

        return sync_profile_datetime(
            self.camera,
            self.profile,
            ref,
            plugin_name=self.name,
            model=self._display_model(),
        )

    def audit_prepared_capture(self, prepared):
        return prepared.token[1]

    @staticmethod
    def _capture_groups(operations):
        """Return indivisible SET...PHOTO groups in execution order."""
        group = []
        for operation in operations:
            group.append(operation)
            if operation.get("action") != "set":
                yield tuple(group)
                group = []
        if group:
            yield tuple(group)

    @staticmethod
    def _group_budget_seconds(group):
        durations = [operation.get("duration_ms") for operation in group]
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in durations
        ):
            return None
        return sum(float(value) for value in durations) / 1000.0

    def _effective_capture_group(self, group):
        """Remove every redundant SET using characterized state dependencies.

        No camera GET is allowed here.  The local state cache is authoritative
        until a SET/capture failure or reconnect.  A setting is resent only if
        its requested value changed, or if a characterized dependency explicitly
        invalidated our knowledge of that setting.
        """
        if not group or group[-1].get("action") == "set":
            return group

        simulated = dict(self._known_settings)
        effective = []

        for operation in group:
            if operation.get("action") != "set":
                effective.append(operation)
                continue

            key = self._SEMANTIC.get(str(operation.get("parameter")))
            if key is None or key not in self.commands:
                return group

            target = self._resolved_value(key, operation.get("value"))
            same = key in simulated and str(simulated[key]) == str(target)
            if same:
                continue

            effective.append(operation)
            simulated[key] = target
            for invalidated in self.commands[key].get("invalidates", []):
                simulated.pop(str(invalidated), None)

        return tuple(effective)

    def trigger_prepared(self, prepared, deadline=None):
        frames = 0
        truncated = False
        first_photo_pending = True
        target_time = getattr(prepared, "target_time", None)
        groups = self._capture_groups(self.audit_prepared_capture(prepared))
        for group in groups:
            effective_group = self._effective_capture_group(group)
            if deadline is not None:
                remaining = seconds_until_deadline(deadline)
                budget = self._group_budget_seconds(effective_group)
                if remaining <= 0 or (
                    budget is not None and remaining < budget
                ):
                    self.log(
                        f"   [{self.name}] deadline: capture group "
                        "truncated before SET (ok)"
                    )
                    truncated = True
                    break

            try:
                for operation in effective_group:
                    if operation["action"] == "set":
                        self.set_parameter(
                            operation["parameter"],
                            operation["value"],
                        )
                    else:
                        # SET preparation may start before the scheduled slot,
                        # but the PHOTO command itself must never be sent early.
                        if first_photo_pending and target_time is not None:
                            remaining = seconds_until_deadline(target_time)
                            while remaining is not None and remaining > 0:
                                time.sleep(min(0.05, remaining))
                                remaining = seconds_until_deadline(target_time)
                            first_photo_pending = False
                        frames += self.execute_photo(operation).frames
            except Exception:
                self._known_settings.clear()
                self._writable_cache.clear()
                raise

        return CaptureResult(
            frames,
            prepared.planned_count,
            detail="deadline" if truncated else None,
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
