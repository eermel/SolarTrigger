"""Report deterministic differences between eclipse datasets and Calculator data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from scripts.eclipse_dataset_builder import (
    DEFAULT_ELEMENTS_PATH,
    DEFAULT_INDEX_PATH,
    DEFAULT_OUTPUT_DIR,
    ELEMENT_NAMES,
    add_elements_with_anomalies,
    discover_eclipses_with_anomalies,
    make_dataset,
)


def _record_difference(
    changed_fields: list[str],
    numeric_deltas: dict[str, dict[str, int | float]],
    field: str,
    old: Any,
    new: Any,
    *,
    numeric: bool,
) -> None:
    if old == new:
        return
    changed_fields.append(field)
    if numeric:
        numeric_deltas[field] = {"old": old, "new": new}


def compare_dataset(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compare the task-scoped fields of two dataset representations."""

    changed_fields: list[str] = []
    numeric_deltas: dict[str, dict[str, int | float]] = {}

    for key in ("file", "option_text", "type"):
        _record_difference(
            changed_fields,
            numeric_deltas,
            f"source.{key}",
            old.get("source", {}).get(key),
            new.get("source", {}).get(key),
            numeric=False,
        )

    for key in ("val", "elements_offset"):
        _record_difference(
            changed_fields,
            numeric_deltas,
            f"jubier.{key}",
            old.get("jubier", {}).get(key),
            new.get("jubier", {}).get(key),
            numeric=True,
        )

    for key in ELEMENT_NAMES:
        _record_difference(
            changed_fields,
            numeric_deltas,
            f"elements.{key}",
            old.get("elements", {}).get(key),
            new.get("elements", {}).get(key),
            numeric=True,
        )

    return {
        "changed_fields": sorted(changed_fields),
        "numeric_deltas": dict(sorted(numeric_deltas.items())),
    }


def diff_datasets(
    data_dir: str | Path = DEFAULT_OUTPUT_DIR,
    index_path: str | Path = DEFAULT_INDEX_PATH,
    elements_path: str | Path = DEFAULT_ELEMENTS_PATH,
) -> list[dict[str, Any]]:
    """Regenerate and compare every dataset named by the on-disk registry."""

    data_path = Path(data_dir)
    registry = json.loads((data_path / "registry.json").read_text(encoding="utf-8"))
    entries = registry["eclipses"]
    if not isinstance(entries, list):
        raise ValueError("registry eclipses must be a list")

    eclipses, anomalies = discover_eclipses_with_anomalies(index_path)
    enriched, element_anomalies = add_elements_with_anomalies(eclipses, elements_path)
    anomalies.extend(element_anomalies)
    if anomalies:
        raise ValueError(f"Calculator source contains anomalies: {anomalies!r}")
    regenerated = {
        eclipse["date"]: make_dataset(eclipse, "ignored") for eclipse in enriched
    }

    reports: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for entry in sorted(entries, key=lambda item: item["date"]):
        date = entry["date"]
        if date in seen_dates:
            raise ValueError(f"duplicate registry date: {date}")
        seen_dates.add(date)
        if date not in regenerated:
            raise ValueError(f"registry date is absent from Calculator source: {date}")
        old = json.loads((data_path / entry["file"]).read_text(encoding="utf-8"))
        comparison = compare_dataset(old, regenerated[date])
        if comparison["changed_fields"]:
            reports.append({"date": date, **comparison})
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diff eclipse datasets against the local Calculator source"
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument("--elements", type=Path, default=DEFAULT_ELEMENTS_PATH)
    args = parser.parse_args(argv)

    try:
        reports = diff_datasets(args.data_dir, args.index, args.elements)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for report in reports:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if reports else 0


if __name__ == "__main__":
    raise SystemExit(main())
