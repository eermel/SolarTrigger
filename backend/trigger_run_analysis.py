from __future__ import annotations

import math
import re
import statistics
import threading
from dataclasses import dataclass, field
from typing import Any


_VERDICT_ORDER = {"PASS": 0, "WARNING": 1, "FAIL": 2}


@dataclass
class _CommandRecord:
    index: int
    rig_id: int
    action: str
    status: str = "pending"
    lateness_ms: float | None = None
    error_code: str | None = None
    reason: str | None = None
    budget_overrun_ms: float | None = None


@dataclass
class TriggerRunAnalysis:
    """Aggregate execution-plan telemetry without issuing camera commands."""

    plan: dict[str, Any]
    plan_name: str = "execution.plan"
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _records: dict[int, _CommandRecord] = field(default_factory=dict, init=False)
    _fatal_error: str | None = field(default=None, init=False)
    _interrupted_rigs: set[int] = field(default_factory=set, init=False)

    _dispatch_re = re.compile(
        r"^EXECUTION_PLAN rig=(?P<rig>\d+) "
        r"action=(?P<action>SET|PHOTO) "
        r"index=(?P<index>\d+) .*?"
        r"lateness_ms=(?P<lateness>[+-]?\d+(?:\.\d+)?)$"
    )
    _skip_re = re.compile(
        r"^WARNING execution_plan rig=(?P<rig>\d+) "
        r"(?P<reason>skip_past|skip_elapsed_after_recovery) "
        r"index=(?P<index>\d+)"
    )
    _photo_lost_re = re.compile(
        r"^WARNING execution_plan rig=(?P<rig>\d+) "
        r"action=PHOTO index=(?P<index>\d+) .*?photo_lost=1"
        r"(?: reason=(?P<reason>[A-Za-z0-9_]+))?"
    )
    _set_failure_re = re.compile(
        r"^WARNING execution_plan rig=(?P<rig>\d+) "
        r"action=SET index=(?P<index>\d+) .*?"
        r"code=(?P<code>\S+) retry_asap=1"
    )
    _recovered_re = re.compile(
        r"^EXECUTION_PLAN rig=(?P<rig>\d+) "
        r"pending_set_(?:recovered|already_effective) .*?"
        r"index=(?P<index>\d+)"
    )
    _superseded_re = re.compile(
        r"^EXECUTION_PLAN rig=(?P<rig>\d+) "
        r"pending_set_superseded .*?"
        r"old_index=(?P<old>\d+) new_index=(?P<new>\d+)"
    )
    _budget_re = re.compile(
        r"^WARNING execution_plan rig=(?P<rig>\d+) "
        r"index=(?P<index>\d+) "
        r"budget_overrun_ms=(?P<elapsed>\d+(?:\.\d+)?)"
    )
    _interrupted_re = re.compile(
        r"^EXECUTION_PLAN rig=(?P<rig>\d+) scheduler interrupted$"
    )

    def __post_init__(self) -> None:
        commands = self.plan.get("_commands_runtime")
        if not isinstance(commands, list):
            raise ValueError("analysis requires a loaded execution plan")

        for command in commands:
            index = command.get("index")
            rig_id = command.get("rig_id")
            action = command.get("action")
            if (
                isinstance(index, int)
                and isinstance(rig_id, int)
                and action in {"SET", "PHOTO"}
            ):
                self._records[index] = _CommandRecord(
                    index=index,
                    rig_id=rig_id,
                    action=action,
                )

    def _record(self, index: int) -> _CommandRecord | None:
        return self._records.get(index)

    def observe(self, message: str) -> None:
        """Consume one existing scheduler log line."""
        if not isinstance(message, str):
            return

        with self._lock:
            match = self._dispatch_re.match(message)
            if match:
                record = self._record(int(match.group("index")))
                if record is not None:
                    record.status = "dispatched"
                    record.lateness_ms = float(match.group("lateness"))
                return

            match = self._skip_re.match(message)
            if match:
                record = self._record(int(match.group("index")))
                if record is not None:
                    record.status = "skipped"
                    record.reason = match.group("reason")
                return

            match = self._photo_lost_re.match(message)
            if match:
                record = self._record(int(match.group("index")))
                if record is not None:
                    record.status = "photo_lost"
                    record.reason = match.group("reason") or "camera_error"
                return

            match = self._set_failure_re.match(message)
            if match:
                record = self._record(int(match.group("index")))
                if record is not None:
                    record.status = "set_failed"
                    record.error_code = match.group("code")
                return

            match = self._recovered_re.match(message)
            if match:
                record = self._record(int(match.group("index")))
                if record is not None:
                    record.status = "recovered"
                return

            match = self._superseded_re.match(message)
            if match:
                record = self._record(int(match.group("old")))
                if record is not None:
                    record.status = "superseded"
                    record.reason = "newer_set"
                return

            match = self._budget_re.match(message)
            if match:
                record = self._record(int(match.group("index")))
                if record is not None:
                    record.budget_overrun_ms = float(match.group("elapsed"))
                return

            match = self._interrupted_re.match(message)
            if match:
                self._interrupted_rigs.add(int(match.group("rig")))

    def mark_fatal(self, exc: BaseException | str) -> None:
        with self._lock:
            self._fatal_error = str(exc)

    @staticmethod
    def _p95(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        rank = max(1, math.ceil(0.95 * len(ordered)))
        return ordered[rank - 1]

    def _rig_report(self, rig_id: int) -> dict[str, Any]:
        records = sorted(
            (
                record
                for record in self._records.values()
                if record.rig_id == rig_id
            ),
            key=lambda record: record.index,
        )

        for record in records:
            if record.status == "dispatched":
                record.status = "success"

        expected = len(records)
        completed = sum(
            record.status in {"success", "recovered"}
            for record in records
        )
        set_expected = sum(record.action == "SET" for record in records)
        photo_expected = sum(record.action == "PHOTO" for record in records)
        set_completed = sum(
            record.action == "SET"
            and record.status in {"success", "recovered"}
            for record in records
        )
        photo_completed = sum(
            record.action == "PHOTO"
            and record.status == "success"
            for record in records
        )
        photo_lost = sum(record.status == "photo_lost" for record in records)
        unresolved_sets = sum(record.status == "set_failed" for record in records)
        skipped = sum(record.status == "skipped" for record in records)
        superseded = sum(record.status == "superseded" for record in records)
        recovered = sum(record.status == "recovered" for record in records)
        pending = sum(record.status == "pending" for record in records)
        budget_overruns = sum(
            record.budget_overrun_ms is not None
            for record in records
        )

        lateness_records = [
            record
            for record in records
            if record.lateness_ms is not None
        ]
        lateness = [record.lateness_ms for record in lateness_records]

        if lateness:
            worst = max(lateness_records, key=lambda record: record.lateness_ms)
            timing = {
                "count": len(lateness),
                "mean_ms": sum(lateness) / len(lateness),
                "median_ms": statistics.median(lateness),
                "p95_ms": self._p95(lateness),
                "max_ms": worst.lateness_ms,
                "worst_index": worst.index,
            }
        else:
            timing = {
                "count": 0,
                "mean_ms": None,
                "median_ms": None,
                "p95_ms": None,
                "max_ms": None,
                "worst_index": None,
            }

        if (
            self._fatal_error is not None
            or photo_lost
            or unresolved_sets
        ):
            verdict = "FAIL"
        elif (
            skipped
            or superseded
            or recovered
            or pending
            or budget_overruns
            or rig_id in self._interrupted_rigs
        ):
            verdict = "WARNING"
        else:
            verdict = "PASS"

        return {
            "rig_id": rig_id,
            "verdict": verdict,
            "expected": expected,
            "completed": completed,
            "coverage_pct": (
                100.0 if expected == 0 else completed * 100.0 / expected
            ),
            "set_expected": set_expected,
            "set_completed": set_completed,
            "photo_expected": photo_expected,
            "photo_completed": photo_completed,
            "photo_lost": photo_lost,
            "unresolved_sets": unresolved_sets,
            "skipped": skipped,
            "superseded": superseded,
            "recovered": recovered,
            "pending": pending,
            "budget_overruns": budget_overruns,
            "timing": timing,
        }

    def finalize(self) -> dict[str, Any]:
        with self._lock:
            rig_ids = sorted({record.rig_id for record in self._records.values()})
            rigs = [self._rig_report(rig_id) for rig_id in rig_ids]

            verdict = "PASS"
            for rig in rigs:
                if _VERDICT_ORDER[rig["verdict"]] > _VERDICT_ORDER[verdict]:
                    verdict = rig["verdict"]
            if self._fatal_error is not None:
                verdict = "FAIL"

            return {
                "plan_name": self.plan_name,
                "verdict": verdict,
                "fatal_error": self._fatal_error,
                "rigs": rigs,
            }


def _fmt_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def format_trigger_run_analysis(report: dict[str, Any]) -> list[str]:
    """Return log lines that are easy to read and easy to suppress upstream."""
    lines = [
        "TRIGGER_RUN_ANALYSIS "
        f"BEGIN plan={report.get('plan_name', 'execution.plan')} "
        f"verdict={report.get('verdict', 'FAIL')}"
    ]

    for rig in report.get("rigs", []):
        timing = rig["timing"]
        lines.append(
            "TRIGGER_RUN_ANALYSIS "
            f"RIG {rig['rig_id']} verdict={rig['verdict']} "
            f"commands={rig['completed']}/{rig['expected']} "
            f"coverage={rig['coverage_pct']:.1f}% "
            f"SET={rig['set_completed']}/{rig['set_expected']} "
            f"PHOTO={rig['photo_completed']}/{rig['photo_expected']} "
            f"lost={rig['photo_lost']} skipped={rig['skipped']} "
            f"recovered={rig['recovered']} superseded={rig['superseded']} "
            f"budget_overruns={rig['budget_overruns']}"
        )
        lines.append(
            "TRIGGER_RUN_ANALYSIS "
            f"RIG {rig['rig_id']} timing "
            f"mean_ms={_fmt_ms(timing['mean_ms'])} "
            f"median_ms={_fmt_ms(timing['median_ms'])} "
            f"p95_ms={_fmt_ms(timing['p95_ms'])} "
            f"max_ms={_fmt_ms(timing['max_ms'])} "
            f"worst_index={timing['worst_index']}"
        )

    if report.get("fatal_error"):
        lines.append(
            "TRIGGER_RUN_ANALYSIS "
            f"FATAL error={report['fatal_error']}"
        )

    lines.append(
        "TRIGGER_RUN_ANALYSIS "
        f"END verdict={report.get('verdict', 'FAIL')}"
    )
    return lines
