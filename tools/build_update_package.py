#!/usr/bin/env python3
"""Build a SolarTrigger ZIP accepted by the web update interface."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

SEMVER_RE = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def validate_version(value: str) -> str:
    version = str(value or "").strip()
    if not SEMVER_RE.fullmatch(version):
        raise ValueError(
            "version must be semantic and human-readable, e.g. 1.0.0 or 1.0.0-rc1"
        )
    return version


def _load_release_builder(repo_root: Path):
    root = str(repo_root.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    from scripts import build_release_package

    return build_release_package


def build_update(repo_root: Path, output: Path, version: str) -> Path:
    repo_root = repo_root.resolve()
    output = output.resolve()
    version = validate_version(version)
    builder = _load_release_builder(repo_root)
    return builder.build_release(repo_root, output, version)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a SolarTrigger update ZIP for the web interface."
    )
    parser.add_argument("version", help="Human release label, e.g. 1.0.1")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="ZIP path (default: dist/solartrigger-update-<version>.zip)",
    )
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    version = validate_version(args.version)
    output = args.output or (
        args.repo_root / "dist" / f"solartrigger-update-{version}.zip"
    )
    result = build_update(args.repo_root, output, version)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
