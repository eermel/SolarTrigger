"""Launch one SolarTrigger-owned indiserver from a JSON driver list."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


def load_driver_config(path: str | Path) -> tuple[int, list[str]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("INDI configuration must be a JSON object")

    port = payload.get("port", 7624)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("INDI port must be an integer from 1 to 65535")

    raw_drivers = payload.get("drivers")
    if not isinstance(raw_drivers, list):
        raise ValueError("INDI drivers must be an array")

    drivers = []
    for value in raw_drivers:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("INDI driver names must be non-empty strings")
        name = value.strip()
        if name not in drivers:
            drivers.append(name)
    return port, drivers


def available_drivers(drivers: list[str]) -> tuple[list[str], list[str]]:
    available = []
    missing = []
    for driver in drivers:
        if shutil.which(driver):
            available.append(driver)
        else:
            missing.append(driver)
    return available, missing


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)

    port, requested = load_driver_config(args.config)
    drivers, missing = available_drivers(requested)

    for driver in missing:
        print(
            f"INDI driver unavailable and skipped: {driver}",
            flush=True,
        )

    if not drivers:
        raise SystemExit("No configured INDI driver executable is installed")

    executable = shutil.which("indiserver")
    if not executable:
        raise SystemExit("indiserver executable is not installed")

    command = [
        executable,
        "-v",
        "-p",
        str(port),
        *drivers,
    ]
    print(
        "Starting SolarTrigger INDI server: " + " ".join(command),
        flush=True,
    )
    os.execv(executable, command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
