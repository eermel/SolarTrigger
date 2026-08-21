"""Discover the eclipses declared by the local Jubier HTML page."""

from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


DEFAULT_INDEX_PATH = Path(__file__).resolve().parent.parent / "jubier_files" / "index.html"
DEFAULT_ELEMENTS_PATH = (
    Path(__file__).resolve().parent.parent
    / "jubier_files"
    / "SolarEclipseTimerSVG_VML.js"
)
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
