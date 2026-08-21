"""Discover the eclipses declared by the local Jubier HTML page."""

from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


DEFAULT_INDEX_PATH = Path(__file__).resolve().parent.parent / "jubier_files" / "index.html"
_LABEL_PATTERN = re.compile(
    r"^\s*(?P<year>\d{4})\s+(?P<month>[A-Za-z]{3})\s+"
    r"(?P<day>\d{1,2})\s+\((?P<type>[TAPH])\)\s*$"
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
