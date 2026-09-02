from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


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
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionPlanError(f"cannot load execution plan: {path}") from exc

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
    ) -> None:
        self.clock = clock
        self.camera = camera_client
        self.log = log_fn

    def apply_initial_state(self, plan: dict[str, Any]) -> None:
        initial = plan.get("initial_state_required", {})

        if not isinstance(initial, dict):
            raise ExecutionPlanError(
                "initial_state_required must be an object"
            )

        normalized_initial = []

        for raw_rig_id, state in initial.items():
            try:
                rig_id = int(raw_rig_id)
            except (TypeError, ValueError) as exc:
                raise ExecutionPlanError(
                    f"invalid initial state rig id: {raw_rig_id!r}"
                ) from exc

            normalized_initial.append((rig_id, raw_rig_id, state))

        for rig_id, raw_rig_id, state in sorted(normalized_initial):

            if rig_id <= 0:
                raise ExecutionPlanError(
                    f"invalid initial state rig id: {raw_rig_id!r}"
                )

            if not isinstance(state, dict):
                raise ExecutionPlanError(
                    f"initial state for RIG {rig_id} must be an object"
                )

            for parameter, value in state.items():
                if not isinstance(parameter, str) or not parameter:
                    raise ExecutionPlanError(
                        f"invalid initial state parameter for RIG {rig_id}"
                    )

                self.camera.set_parameter(
                    rig_id,
                    parameter,
                    value,
                )

    def prepare_for_execution(self, plan: dict[str, Any]) -> None:
        """Establish the physical state required at the current UTC time.

        The initial state is always established first. When execution starts
        after some commands have already expired, past SET operations are
        replayed in their original order so the camera reaches the state that
        the original uninterrupted timeline would have produced.

        Past PHOTO operations are never replayed.
        """
        self.apply_initial_state(plan)

        commands = plan.get("_commands_runtime")
        if not isinstance(commands, list):
            raise ExecutionPlanError(
                "execution plan was not loaded by runtime loader"
            )

        for command in commands:
            # Camera SET calls themselves take time. Re-evaluate the clock for
            # every command so SETs that expire during preparation are also
            # incorporated into the reconstructed state.
            if self.clock.remaining(command["time"]) >= 0:
                break

            if command["action"] != "SET":
                continue

            self.log(
                f"EXECUTION_PLAN resume_state "
                f"rig={command['rig_id']} "
                f"index={command['index']} "
                f"time={command['time'].isoformat()}Z"
            )

            self._execute_command(command)

    def _wait_until(self, target: datetime) -> bool:
        remaining = self.clock.remaining(target)

        if remaining <= 0:
            return False

        # Fine enough for USB dispatch timing without busy-waiting.
        while remaining > 0:
            self.clock.sleep(min(remaining, 0.02))
            remaining = self.clock.remaining(target)

        return True

    def _execute_command(self, command: dict[str, Any]) -> None:
        rig_id = command["rig_id"]
        action = command["action"]
        params = command["params"]

        if action == "SET":
            self.camera.set_parameter(
                rig_id,
                params["parameter"],
                params["value"],
                fallback_parameter=params.get("fallback_parameter"),
            )
            return

        if action == "PHOTO":
            self.camera.execute_photo(rig_id, params)
            return

        raise ExecutionPlanError(f"unsupported action: {action}")

    def _run_rig(self, rig_id: int, commands: list[dict[str, Any]]) -> None:
        for command in commands:
            target = command["time"]

            # Reprise absolue : aucune commande passée n'est rejouée.
            if self.clock.remaining(target) < 0:
                self.log(
                    f"WARNING execution_plan rig={rig_id} "
                    f"skip_past index={command['index']} "
                    f"time={target.isoformat()}Z"
                )
                continue

            self._wait_until(target)

            self.log(
                f"EXECUTION_PLAN rig={rig_id} "
                f"action={command['action']} "
                f"time={target.isoformat()}Z"
            )

            self._execute_command(command)

    def run(self, plan: dict[str, Any]) -> None:
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

        if errors:
            rig_id, exc = errors[0]
            raise ExecutionPlanError(
                f"execution failed on RIG {rig_id}: {exc}"
            ) from exc
