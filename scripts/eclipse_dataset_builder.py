"""Discover the eclipses declared by the local Jubier HTML page."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


DEFAULT_INDEX_PATH = Path(__file__).resolve().parent.parent / "jubier_files" / "index.html"
DEFAULT_ELEMENTS_PATH = (
    Path(__file__).resolve().parent.parent
    / "jubier_files"
    / "SolarEclipseTimerSVG_VML.js"
)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "eclipses"
SOURCE_INDEX_FILE = "jubier_files/index.html"
_LABEL_PATTERN = re.compile(
    r"^\s*(?P<year>\d{4})\s+(?P<month>[A-Za-z]{3})\s+"
    r"(?P<day>\d{1,2})\s+\((?P<type>[TAPH])\)\s*$"
)
_ELEMENTS_DECLARATION_PATTERN = re.compile(
    r"\bvar\s+elements\s*=\s*new\s+Array\s*\((?P<body>.*?)\)\s*;",
    re.DOTALL,
)
_JS_COMMENT_PATTERN = re.compile(r"//[^\r\n]*|/\*.*?\*/", re.DOTALL)
_NUMBER_PATTERN = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
)
ELEMENTS_PER_ECLIPSE = 28
ELEMENTS_VAL_BIAS = 65
ELEMENT_NAMES = (
    "julian_day",
    "t0",
    "tmin",
    "tmax",
    "dUTC",
    "dT",
    "x0",
    "x1",
    "x2",
    "x3",
    "y0",
    "y1",
    "y2",
    "y3",
    "d0",
    "d1",
    "d2",
    "m0",
    "m1",
    "m2",
    "l10",
    "l11",
    "l12",
    "l20",
    "l21",
    "l22",
    "tan_f1",
    "tan_f2",
)


class _EclipseIndexParser(HTMLParser):
    """Collect options belonging only to ``select#eclipse_index``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_eclipse_select = False
        self._option_value: str | None = None
        self._option_text: list[str] | None = None
        self.options: list[tuple[str | None, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "select":
            self._in_eclipse_select = attributes.get("id") == "eclipse_index"
        elif tag == "option" and self._in_eclipse_select:
            self._option_value = attributes.get("value")
            self._option_text = []

    def handle_data(self, data: str) -> None:
        if self._option_text is not None:
            self._option_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self._option_text is not None:
            self.options.append((self._option_value, "".join(self._option_text)))
            self._option_value = None
            self._option_text = None
        elif tag == "select" and self._in_eclipse_select:
            self._in_eclipse_select = False


def _parse_option(value: str | None, label: str, option_index: int) -> dict[str, Any]:
    match = _LABEL_PATTERN.fullmatch(label)
    if match is None:
        raise ValueError("option label does not contain a supported date and eclipse type")

    try:
        val = int(value) if value is not None else None
    except ValueError as exc:
        raise ValueError("option value is not an integer") from exc
    if val is None:
        raise ValueError("option has no value")

    date_text = " ".join(
        (match.group("year"), match.group("month").title(), match.group("day"))
    )
    try:
        eclipse_date = datetime.strptime(date_text, "%Y %b %d").date().isoformat()
    except ValueError as exc:
        raise ValueError("option date is invalid") from exc

    return {
        "val": val,
        "date": eclipse_date,
        "type": match.group("type"),
        "label": label,
        "option_index": option_index,
    }


def discover_eclipses_with_anomalies(
    index_path: str | Path = DEFAULT_INDEX_PATH,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return valid eclipses and malformed options from ``select#eclipse_index``.

    An invalid option is reported in ``anomalies`` and is never represented as an
    eclipse. Positions are zero-based indexes within the selected option list.
    """

    parser = _EclipseIndexParser()
    parser.feed(Path(index_path).read_text(encoding="utf-8"))
    parser.close()

    eclipses: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    for option_index, (value, label) in enumerate(parser.options):
        try:
            eclipses.append(_parse_option(value, label, option_index))
        except ValueError as exc:
            anomalies.append(
                {
                    "value": value,
                    "label": label,
                    "option_index": option_index,
                    "error": str(exc),
                }
            )
    return eclipses, anomalies


def discover_eclipses(index_path: str | Path = DEFAULT_INDEX_PATH) -> list[dict[str, Any]]:
    """Return all valid eclipses in their original option order."""

    eclipses, _anomalies = discover_eclipses_with_anomalies(index_path)
    return eclipses


def parse_elements_array(javascript: str) -> list[float]:
    """Parse the numeric values from the JavaScript ``elements`` array.

    Comments and whitespace may occur between values. Any other token is
    rejected instead of being interpreted as JavaScript.
    """

    declaration = _ELEMENTS_DECLARATION_PATTERN.search(javascript)
    if declaration is None:
        raise ValueError("elements array declaration not found")

    body = _JS_COMMENT_PATTERN.sub("", declaration.group("body"))
    fields = body.split(",")
    if fields and not fields[-1].strip():
        fields.pop()

    elements: list[float] = []
    for element_index, field in enumerate(fields):
        token = field.strip()
        if not _NUMBER_PATTERN.fullmatch(token):
            raise ValueError(
                f"elements array value at index {element_index} is not numeric: "
                f"{token!r}"
            )
        elements.append(float(token))
    return elements


def load_elements_array(
    elements_path: str | Path = DEFAULT_ELEMENTS_PATH,
) -> list[float]:
    """Load and parse the JavaScript eclipse-elements array."""

    return parse_elements_array(Path(elements_path).read_text(encoding="utf-8"))


def extract_elements_slice(elements: list[float], val: int) -> tuple[int, list[float]]:
    """Return ``(elements_offset, 28-value slice)`` for one eclipse value."""

    elements_offset = ELEMENTS_PER_ECLIPSE * (val + ELEMENTS_VAL_BIAS)
    slice_end = elements_offset + ELEMENTS_PER_ECLIPSE
    if elements_offset < 0 or slice_end > len(elements):
        raise ValueError(
            f"elements slice [{elements_offset}:{slice_end}] exceeds array length "
            f"{len(elements)}"
        )
    return elements_offset, elements[elements_offset:slice_end]


def extract_eclipse_elements(
    val: int,
    elements_path: str | Path = DEFAULT_ELEMENTS_PATH,
) -> tuple[int, list[float]]:
    """Load the local source and extract the elements for one eclipse."""

    return extract_elements_slice(load_elements_array(elements_path), val)


def add_elements_with_anomalies(
    eclipses: list[dict[str, Any]],
    elements_path: str | Path = DEFAULT_ELEMENTS_PATH,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach element slices, excluding eclipses whose slice cannot be read."""

    try:
        elements = load_elements_array(elements_path)
    except (OSError, ValueError) as exc:
        return [], [{"error": str(exc)}]

    enriched: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    for eclipse in eclipses:
        try:
            elements_offset, element_slice = extract_elements_slice(
                elements, eclipse["val"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            anomalies.append({"eclipse": eclipse, "error": str(exc)})
            continue
        enriched.append(
            {
                **eclipse,
                "elements_offset": elements_offset,
                "elements": element_slice,
            }
        )
    return enriched, anomalies


def structure_elements(elements: list[float]) -> dict[str, float]:
    """Associate one complete element slice with the documented symbols."""

    if len(elements) != len(ELEMENT_NAMES):
        raise ValueError(
            f"expected {len(ELEMENT_NAMES)} eclipse elements, got {len(elements)}"
        )
    return dict(zip(ELEMENT_NAMES, elements, strict=True))


def _generated_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def make_dataset(eclipse: dict[str, Any], generated_utc: str) -> dict[str, Any]:
    """Create the public JSON representation of an enriched eclipse."""

    return {
        "header": {
            "generated_utc": generated_utc,
            "date_iso": eclipse["date"],
        },
        "jubier": {
            "val": eclipse["val"],
            "elements_offset": eclipse["elements_offset"],
        },
        "source": {
            "file": SOURCE_INDEX_FILE,
            "type": "index_option",
            "option_text": eclipse["label"],
            "option_index": eclipse["option_index"],
        },
        "elements": structure_elements(eclipse["elements"]),
    }


def _write_json(path: Path, content: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _registry_entry(eclipse: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": eclipse["date"],
        "file": f'{eclipse["date"]}.json',
        "val": eclipse["val"],
        "option_index": eclipse["option_index"],
        "elements_offset": eclipse["elements_offset"],
    }


def build_all(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    index_path: str | Path = DEFAULT_INDEX_PATH,
    elements_path: str | Path = DEFAULT_ELEMENTS_PATH,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate every valid eclipse dataset and its registry."""

    eclipses, anomalies = discover_eclipses_with_anomalies(index_path)
    enriched, element_anomalies = add_elements_with_anomalies(eclipses, elements_path)
    anomalies.extend(element_anomalies)

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    generated_utc = _generated_utc()
    for eclipse in enriched:
        _write_json(
            destination / f'{eclipse["date"]}.json',
            make_dataset(eclipse, generated_utc),
        )

    registry = {
        "generated_utc": generated_utc,
        "eclipses": [_registry_entry(eclipse) for eclipse in enriched],
    }
    _write_json(destination / "registry.json", registry)
    return enriched, anomalies


def build_one(
    date_iso: str,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    index_path: str | Path = DEFAULT_INDEX_PATH,
    elements_path: str | Path = DEFAULT_ELEMENTS_PATH,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate the dataset for one discovered ISO date."""

    eclipses, anomalies = discover_eclipses_with_anomalies(index_path)
    matches = [eclipse for eclipse in eclipses if eclipse["date"] == date_iso]
    if not matches:
        anomalies.append({"date_iso": date_iso, "error": "eclipse date not found"})
        return [], anomalies

    enriched, element_anomalies = add_elements_with_anomalies(matches, elements_path)
    anomalies.extend(element_anomalies)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    generated_utc = _generated_utc()
    for eclipse in enriched:
        _write_json(
            destination / f'{eclipse["date"]}.json',
            make_dataset(eclipse, generated_utc),
        )
    return enriched, anomalies


def _print_report(generated: list[dict[str, Any]], anomalies: list[dict[str, Any]]) -> None:
    print(f"Generated eclipses: {len(generated)}")
    if anomalies:
        print("Skipped eclipses:")
        for anomaly in anomalies:
            print(json.dumps(anomaly, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build local Jubier eclipse datasets")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list discovered eclipses")
    subparsers.add_parser("build-all", help="build every valid eclipse")
    build_one_parser = subparsers.add_parser("build-one", help="build one ISO date")
    build_one_parser.add_argument("date", help="eclipse date in YYYY-MM-DD format")
    args = parser.parse_args(argv)

    if args.command == "list":
        eclipses, anomalies = discover_eclipses_with_anomalies()
        for eclipse in eclipses:
            print(
                f'{eclipse["date"]} val={eclipse["val"]} '
                f'option_index={eclipse["option_index"]} {eclipse["label"]}'
            )
        _print_report([], anomalies)
        return 1 if anomalies else 0

    if args.command == "build-all":
        generated, anomalies = build_all()
    else:
        generated, anomalies = build_one(args.date)
    _print_report(generated, anomalies)
    return 1 if anomalies or not generated else 0


if __name__ == "__main__":
    raise SystemExit(main())
