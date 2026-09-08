from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.execution_plan_text import (
    ExecutionPlanTextError,
    parse_execution_plan_text,
)


class ExecutionPlanError(RuntimeError):
    pass


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ExecutionPlanError("command time_utc must be an explicit UTC Z timestamp")

    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ExecutionPlanError(f"invalid command time_utc: {value!r}") from exc

    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ExecutionPlanError("command time_utc must be UTC")

    # RuntimeClock exposes naive UTC datetimes.
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def load_execution_plan(path: str | Path) -> dict[str, Any]:
    path = Path(path)

    try:
        raw = path.read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        raise ExecutionPlanError(
            f"cannot load execution plan: {path}"
        ) from exc

    if path.suffix.lower() == ".plan":
        try:
            plan = parse_execution_plan_text(
                raw
            )
        except ExecutionPlanTextError as exc:
            raise ExecutionPlanError(
                f"cannot load execution plan: "
                f"{path}: {exc}"
            ) from exc
    else:
        try:
            plan = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExecutionPlanError(
                f"cannot load execution plan: "
                f"{path}"
            ) from exc

    if not isinstance(plan, dict):
        raise ExecutionPlanError("execution plan root must be an object")

    if plan.get("schema_version") != 2:
        raise ExecutionPlanError("unsupported execution plan schema")

    if plan.get("config_type") != "execution_plan":
        raise ExecutionPlanError("invalid execution plan type")

    commands = plan.get("commands")
    if not isinstance(commands, list):
        raise ExecutionPlanError("execution plan commands must be an array")

    normalized = []

    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            raise ExecutionPlanError(f"command {index} must be an object")

        if set(command) != {"time_utc", "rig_id", "action", "params"}:
            raise ExecutionPlanError(f"command {index} has invalid fields")

        when = _parse_utc(command["time_utc"])

        rig_id = command["rig_id"]
        if isinstance(rig_id, bool) or not isinstance(rig_id, int) or rig_id <= 0:
            raise ExecutionPlanError(f"command {index} has invalid rig_id")

        action = command["action"]
        if action not in {"SET", "PHOTO"}:
            raise ExecutionPlanError(f"command {index} has invalid action")

        params = command["params"]
        if not isinstance(params, dict):
            raise ExecutionPlanError(f"command {index} params must be an object")

        if action == "SET":
            parameter = params.get("parameter")
            if not isinstance(parameter, str) or not parameter:
                raise ExecutionPlanError(
                    f"command {index} SET requires parameter"
                )
            if "value" not in params:
                raise ExecutionPlanError(
                    f"command {index} SET requires value"
                )
            fallback = params.get("fallback_parameter")
            if fallback is not None and (
                not isinstance(fallback, str) or not fallback
            ):
                raise ExecutionPlanError(
                    f"command {index} has invalid fallback_parameter"
                )

        normalized.append(
            {
                "time": when,
                "rig_id": rig_id,
                "action": action,
                "params": dict(params),
                "index": index,
            }
        )

    # Compiler normally emits this order. Reject disorder rather than silently
    # changing the execution contract.
    previous = None
    for command in normalized:
        key = (command["time"], command["rig_id"])
        if previous is not None and key < previous:
            raise ExecutionPlanError("execution plan commands are not ordered")
        previous = key

    result = dict(plan)
    result["_commands_runtime"] = normalized
    return result


def rebase_execution_plan(
    plan: dict[str, Any],
    new_sequence_start: datetime,
) -> dict[str, Any]:
    """Translate one loaded execution plan by one uniform UTC offset."""
    commands = plan.get("_commands_runtime")
    if not isinstance(commands, list):
        raise ExecutionPlanError(
            "execution plan was not loaded by runtime loader"
        )

    sequence_start_raw = plan.get("sequence_start_utc")
    sequence_end_raw = plan.get("sequence_end_utc")

    sequence_start = _parse_utc(sequence_start_raw)
    sequence_end = _parse_utc(sequence_end_raw)

    if new_sequence_start.tzinfo is not None:
        new_sequence_start = (
            new_sequence_start
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )

    delta = new_sequence_start - sequence_start

    rebased = dict(plan)

    rebased_runtime_commands = [
        {
            **command,
            "time": command["time"] + delta,
        }
        for command in commands
    ]
    rebased["_commands_runtime"] = rebased_runtime_commands

    public_commands = plan.get("commands")
    if not isinstance(public_commands, list):
        raise ExecutionPlanError(
            "execution plan commands must be an array"
        )

    rebased["commands"] = [
        {
            **command,
            "time_utc": runtime_command["time"]
            .replace(tzinfo=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }
        for command, runtime_command in zip(
            public_commands,
            rebased_runtime_commands,
            strict=True,
        )
    ]

    rebased["sequence_start_utc"] = (
        new_sequence_start
        .replace(tzinfo=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )

    rebased["sequence_end_utc"] = (
        (sequence_end + delta)
        .replace(tzinfo=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )

    return rebased


class ExecutionPlanRuntime:
    """Execute one schema-v2 plan with an independent timeline per RIG."""

    def __init__(
        self,
        *,
        clock,
        camera_client,
        log_fn: Callable[[str], None] = print,
        stop_event=None,
    ) -> None:
        self.clock = clock
        self.camera = camera_client
        self.log = log_fn
        self.stop_event = stop_event
        self._timing_samples: list[float] = []
        self._timing_lock = threading.Lock()

    @staticmethod
    def _normalize_initial_state(plan: dict[str, Any]) -> dict[int, dict[str, Any]]:
        initial = plan.get("initial_state_required", {})
        if not isinstance(initial, dict):
            raise ExecutionPlanError(
                "initial_state_required must be an object"
            )

        result: dict[int, dict[str, Any]] = {}
        for raw_rig_id, state in initial.items():
            try:
                rig_id = int(raw_rig_id)
            except (TypeError, ValueError) as exc:
                raise ExecutionPlanError(
                    f"invalid initial state rig id: {raw_rig_id!r}"
                ) from exc
            if rig_id <= 0:
                raise ExecutionPlanError(
                    f"invalid initial state rig id: {raw_rig_id!r}"
                )
            if not isinstance(state, dict):
                raise ExecutionPlanError(
                    f"initial state for RIG {rig_id} must be an object"
                )
            normalized = {}
            for parameter, value in state.items():
                if not isinstance(parameter, str) or not parameter:
                    raise ExecutionPlanError(
                        f"invalid initial state parameter for RIG {rig_id}"
                    )
                normalized[parameter] = value
            result[rig_id] = normalized
        return result

    def _effective_state_at_current_time(
        self,
        plan: dict[str, Any],
    ) -> dict[int, dict[str, Any]]:
        """Return desired camera state now, without replaying command history."""
        states = {
            rig_id: dict(state)
            for rig_id, state in self._normalize_initial_state(plan).items()
        }
        commands = plan.get("_commands_runtime")
        if not isinstance(commands, list):
            raise ExecutionPlanError(
                "execution plan was not loaded by runtime loader"
            )

        # Ensure baseline preflight runs for every RIG present in the plan,
        # even when there is no initial_state_required entry and no expired SET.
        for command in commands:
            states.setdefault(command["rig_id"], {})

        current = self.clock.now()
        for command in commands:
            if command["time"] >= current:
                break
            if command["action"] != "SET":
                continue
            parameter = command["params"].get("parameter")
            # Old contract-v2 capture_setup is a transient macro, not a stable
            # state.  Never reconstruct it at START/restart.
            if not isinstance(parameter, str) or parameter == "capture_setup":
                continue
            states.setdefault(command["rig_id"], {})[parameter] = (
                command["params"].get("value")
            )
        return states

    def apply_initial_state(self, plan: dict[str, Any]) -> None:
        """Apply only the declared initial snapshot.

        Kept as a compatibility/public helper for callers and tests.  Normal
        Trigger startup uses :meth:`prepare_for_execution`, which additionally
        reduces expired SET history to the effective current state instead of
        replaying it.
        """
        states = self._normalize_initial_state(plan)
        preflight = getattr(self.camera, "preflight", None)
        for rig_id, state in sorted(states.items()):
            if callable(preflight):
                preflight(rig_id, state)
                continue
            for parameter, value in state.items():
                self.camera.set_parameter(rig_id, parameter, value)

    def prepare_for_execution(self, plan: dict[str, Any]) -> None:
        """Strict camera preflight before the timed scheduler starts.

        A late START does not replay expired SET/PHOTO commands.  It reduces
        them to the single effective state required *now*, then asks the camera
        endpoint to GET/SET/check that state once.  Characterized GET-only
        invariants (for example Sony A6600 manual mode) fail here with an
        operator-actionable message, therefore the RIG sequence never starts in
        an invalid physical configuration.
        """
        states = self._effective_state_at_current_time(plan)
        preflight = getattr(self.camera, "preflight", None)

        for rig_id, state in sorted(states.items()):
            self.log(
                f"EXECUTION_PLAN preflight rig={rig_id} "
                f"state_keys={','.join(sorted(state)) or 'none'}"
            )
            if callable(preflight):
                preflight(rig_id, state)
                continue

            # Compatibility for older test/fake endpoints.  This still applies
            # one reduced state snapshot and never replays historical commands.
            for parameter, value in state.items():
                self.camera.set_parameter(rig_id, parameter, value)

    def _stop_requested(self) -> bool:
        return (
            self.stop_event is not None
            and self.stop_event.is_set()
        )

    def _wait_until(self, target: datetime) -> bool:
        remaining = self.clock.remaining(target)

        if remaining <= 0:
            return False

        # Fine enough for USB dispatch timing without busy-waiting.
        while remaining > 0:
            if self._stop_requested():
                return False

            self.clock.sleep(min(remaining, 0.02))
            remaining = self.clock.remaining(target)

        return not self._stop_requested()

    def _execute_command(self, command: dict[str, Any]) -> None:
        rig_id = command["rig_id"]
        action = command["action"]
        params = command["params"]

        options = {}
        if params.get("timing_contract_version") == 2:
            # Transport allowance is not an additional scheduling reservation.
            options["timeout_s"] = max(5.0, float(params["duration_ms"]) / 1000 + 1.0)
            options["scheduled"] = True
        if action == "SET":
            self.camera.set_parameter(
                rig_id,
                params["parameter"],
                params["value"],
                fallback_parameter=params.get("fallback_parameter"),
                **options,
            )
            return

        if action == "PHOTO":
            self.camera.execute_photo(rig_id, params, **options)
            return

        raise ExecutionPlanError(f"unsupported action: {action}")

    @staticmethod
    def _camera_error_code(exc: BaseException) -> str:
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code:
            return code
        return type(exc).__name__

    @staticmethod
    def _remember_pending_set(
        pending_sets: dict[str, dict[str, Any]],
        command: dict[str, Any],
    ) -> None:
        parameter = command["params"]["parameter"]

        # Last desired value wins. Reinsert it so dict order also reflects
        # the chronological order of the most recent SET commands.
        pending_sets.pop(parameter, None)
        pending_sets[parameter] = command

    def _reconcile_pending_sets(
        self,
        rig_id: int,
        pending_sets: dict[str, dict[str, Any]],
    ) -> bool:
        """Converge only SETs that actually failed during this run.

        GET is attempted first.  If the failed USB transaction did reach the
        camera, no duplicate SET is sent.  Otherwise the latest desired value
        is retried immediately.  Past plan commands that were never attempted
        are never inserted here.
        """
        getter = getattr(self.camera, "get_parameter", None)

        for parameter, command in list(pending_sets.items()):
            desired = command["params"].get("value")

            if callable(getter):
                try:
                    actual = getter(rig_id, parameter)
                except Exception as exc:
                    self.log(
                        f"WARNING execution_plan rig={rig_id} "
                        f"pending_get_failed parameter={parameter} "
                        f"code={self._camera_error_code(exc)}"
                    )
                else:
                    if str(actual) == str(desired):
                        pending_sets.pop(parameter, None)
                        self.log(
                            f"EXECUTION_PLAN rig={rig_id} "
                            f"pending_set_already_effective "
                            f"parameter={parameter} value={desired} "
                            f"index={command['index']}"
                        )
                        continue

            try:
                self._execute_command(command)
            except ExecutionPlanError:
                raise
            except Exception as exc:
                self.log(
                    f"WARNING execution_plan rig={rig_id} "
                    f"pending_set_retry_failed parameter={parameter} "
                    f"index={command['index']} "
                    f"code={self._camera_error_code(exc)}"
                )
                return False

            pending_sets.pop(parameter, None)
            self.log(
                f"EXECUTION_PLAN rig={rig_id} "
                f"pending_set_recovered parameter={parameter} "
                f"index={command['index']}"
            )

        return not pending_sets

    def _run_rig(self, rig_id: int, commands: list[dict[str, Any]]) -> None:
        pending_sets: dict[str, dict[str, Any]] = {}
        guarded = any(
            c["params"].get("timing_contract_version") == 2
            for c in commands
        )

        for command in commands:
            if self._stop_requested():
                self.log(
                    f"EXECUTION_PLAN rig={rig_id} scheduler interrupted"
                )
                return

            target = command["time"]

            # Absolute timeline: an unattempted command whose timestamp is in
            # the past is permanently discarded.  It is never converted into
            # pending work and can therefore never be replayed later.
            if self.clock.remaining(target) < 0:
                self.log(
                    f"WARNING execution_plan rig={rig_id} "
                    f"skip_past index={command['index']} "
                    f"time={target.isoformat()}Z"
                )
                continue

            # If the next planned SET changes the same parameter again, its
            # newer desired value supersedes the older failed request.  There
            # is no reason to recover a state that will never be used by a
            # future PHOTO.
            if command["action"] == "SET":
                parameter = command["params"].get("parameter")
                previous = pending_sets.get(parameter)
                if (
                    previous is not None
                    and str(previous["params"].get("value"))
                    != str(command["params"].get("value"))
                ):
                    pending_sets.pop(parameter, None)
                    self.log(
                        f"EXECUTION_PLAN rig={rig_id} "
                        f"pending_set_superseded parameter={parameter} "
                        f"old_index={previous['index']} "
                        f"new_index={command['index']}"
                    )

            # A SET that really failed earlier may be retried ASAP.  Reconcile
            # before waiting so a transient USB outage/battery change can heal
            # while there is still useful time before the next action.
            if pending_sets:
                self._reconcile_pending_sets(rig_id, pending_sets)
                if self.clock.remaining(target) < 0:
                    self.log(
                        f"WARNING execution_plan rig={rig_id} "
                        f"skip_elapsed_after_recovery index={command['index']}"
                    )
                    continue

            self._wait_until(target)

            if self._stop_requested():
                self.log(
                    f"EXECUTION_PLAN rig={rig_id} scheduler interrupted"
                )
                return

            # Normal scheduler wake-up can be a few milliseconds late.  That is
            # still the current command, not catch-up work.  We only reject a
            # target after an explicit recovery operation has consumed its slot.
            dispatch_time = self.clock.now()
            lateness_ms = (
                dispatch_time - target
            ).total_seconds() * 1000.0

            with self._timing_lock:
                self._timing_samples.append(lateness_ms)

            action = command["action"]
            self.log(
                f"EXECUTION_PLAN rig={rig_id} "
                f"action={action} "
                f"index={command['index']} "
                f"scheduled={target.isoformat()}Z "
                f"dispatch={dispatch_time.isoformat()}Z "
                f"lateness_ms={lateness_ms:+.3f}"
            )

            if action == "PHOTO" and pending_sets:
                if not self._reconcile_pending_sets(rig_id, pending_sets):
                    self.log(
                        f"WARNING execution_plan rig={rig_id} "
                        f"action=PHOTO index={command['index']} "
                        f"photo_lost=1 reason=unapplied_settings"
                    )
                    continue
                if self.clock.remaining(target) < 0:
                    self.log(
                        f"WARNING execution_plan rig={rig_id} "
                        f"action=PHOTO index={command['index']} "
                        f"photo_lost=1 reason=recovery_too_late"
                    )
                    continue

            try:
                self._execute_command(command)

            except ExecutionPlanError:
                # Structural/programming errors remain fatal.  USB/IPC camera
                # errors are operation-scoped and are handled below.
                raise

            except Exception as exc:
                code = self._camera_error_code(exc)

                if action == "SET":
                    self._remember_pending_set(pending_sets, command)
                    self.log(
                        f"WARNING execution_plan rig={rig_id} "
                        f"action=SET index={command['index']} "
                        f"parameter={command['params']['parameter']} "
                        f"code={code} retry_asap=1"
                    )
                    # One immediate convergence attempt.  If the body is still
                    # absent, pending state remains and later future commands
                    # will try again; the scheduler itself never terminates.
                    self._reconcile_pending_sets(rig_id, pending_sets)
                else:
                    # Never replay PHOTO.  The shutter may have fired before a
                    # transport failure made the result unknowable.
                    self.log(
                        f"WARNING execution_plan rig={rig_id} "
                        f"action=PHOTO index={command['index']} "
                        f"code={code} photo_lost=1; "
                        "continuing absolute timeline"
                    )
                continue

            if guarded:
                elapsed_ms = (
                    self.clock.now() - dispatch_time
                ).total_seconds() * 1000.0
                if elapsed_ms > float(
                    command["params"].get("duration_ms", float("inf"))
                ):
                    self.log(
                        f"WARNING execution_plan rig={rig_id} "
                        f"index={command['index']} "
                        f"budget_overrun_ms={elapsed_ms:.1f}; "
                        "elapsed commands will be skipped"
                    )

            if action == "SET":
                # A newer successful SET proves the desired state and
                # supersedes any older failed request for this parameter.
                pending_sets.pop(
                    command["params"]["parameter"],
                    None,
                )

    def run(self, plan: dict[str, Any]) -> None:
        with self._timing_lock:
            self._timing_samples = []

        commands = plan.get("_commands_runtime")
        if not isinstance(commands, list):
            raise ExecutionPlanError("execution plan was not loaded by runtime loader")

        by_rig: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for command in commands:
            by_rig[command["rig_id"]].append(command)

        errors: list[tuple[int, BaseException]] = []
        errors_lock = threading.Lock()

        def runner(rig_id: int, rig_commands: list[dict[str, Any]]) -> None:
            try:
                self._run_rig(rig_id, rig_commands)
            except BaseException as exc:
                with errors_lock:
                    errors.append((rig_id, exc))

        threads = [
            threading.Thread(
                target=runner,
                args=(rig_id, rig_commands),
                name=f"execution-plan-rig-{rig_id}",
            )
            for rig_id, rig_commands in sorted(by_rig.items())
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        with self._timing_lock:
            samples = list(self._timing_samples)

        if samples:
            ordered = sorted(samples)
            count = len(ordered)
            mean_ms = sum(ordered) / count

            # Nearest-rank percentile: ceil(0.95 * count), converted to index.
            p95_index = max(0, (95 * count + 99) // 100 - 1)
            p95_ms = ordered[p95_index]
            max_ms = ordered[-1]

            self.log(
                f"EXECUTION_PLAN TIMING "
                f"count={count} "
                f"mean_ms={mean_ms:+.3f} "
                f"p95_ms={p95_ms:+.3f} "
                f"max_ms={max_ms:+.3f}"
            )

        if errors:
            rig_id, exc = errors[0]
            raise ExecutionPlanError(
                f"execution failed on RIG {rig_id}: {exc}"
            ) from exc
