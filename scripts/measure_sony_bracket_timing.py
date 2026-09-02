#!/usr/bin/env python3
"""
Measure real Sony native-bracket timing.

NO estimation is used here. The script measures the actual operations used by
plugins/camera/sony.py:

    Single Shot
    centre shutter
    Continuous Bracket
    bulb=1
    wait for FILE_ADDED frames
    bulb=0
    settle idle

The measurements are intended to characterize one high-level atomic PHOTO
operation for the Sequencer.

WARNING:
    Running this script triggers the physical camera and writes photographs
    to the camera card.

Example:
    python3 scripts/measure_sony_bracket_timing.py --n 3

By default, 3/5/7/9-frame 1 EV brackets are measured.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import gphoto2 as gp
except ImportError:
    print("ERROR: python gphoto2 module is not installed")
    sys.exit(1)

from plugins.camera.sony import SonyPlugin
from plugins.camera import sony_planner as planner


BRACKETS = {
    3: [
        "1/500",
        "1/250",
        "1/125",
    ],
    5: [
        "1/1000",
        "1/500",
        "1/250",
        "1/125",
        "1/60",
    ],
    7: [
        "1/2000",
        "1/1000",
        "1/500",
        "1/250",
        "1/125",
        "1/60",
        "1/30",
    ],
    9: [
        "1/4000",
        "1/2000",
        "1/1000",
        "1/500",
        "1/250",
        "1/125",
        "1/60",
        "1/30",
        "1/15",
    ],
}


def kill_desktop_camera_mounts():
    subprocess.run(
        ["killall", "gvfsd-gphoto2", "gvfsd"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    time.sleep(0.5)


def detect_camera():
    cameras = gp.Camera.autodetect()

    if not cameras:
        raise RuntimeError("no camera detected")

    print("Detected cameras:")
    for index, (name, port) in enumerate(cameras):
        print(f"  [{index}] {name} @ {port}")

    name, port = cameras[0]

    if "ILCE-7M5" not in name.upper():
        raise RuntimeError(
            f"first camera is not Sony ILCE-7M5: {name!r}"
        )

    abilities = gp.CameraAbilitiesList()
    abilities.load()

    ports = gp.PortInfoList()
    ports.load()

    camera = gp.Camera()
    camera.set_abilities(
        abilities[abilities.lookup_model(name)]
    )
    camera.set_port_info(
        ports[ports.lookup_path(port)]
    )
    camera.init()

    return camera, name, port


def require_ok(result, description):
    ok, readonly, error = result

    if ok:
        return

    kind = "read-only" if readonly else "error"
    raise RuntimeError(
        f"{description}: {kind}: {error}"
    )


def ms(start, end):
    return (end - start) * 1000.0


def percentile(values, fraction):
    ordered = sorted(values)

    if not ordered:
        return None

    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def stats(values):
    if not values:
        return {}

    return {
        "count": len(values),
        "min_ms": min(values),
        "median_ms": statistics.median(values),
        "mean_ms": statistics.mean(values),
        "p95_ms": percentile(values, 0.95),
        "max_ms": max(values),
    }


def measure_one(plugin, frame_count, views):
    centre = views[len(views) // 2]

    bracket = planner.Bracket(
        centre=centre,
        step=1.0,
        nimg=frame_count,
        views=views,
    )

    result = {
        "frames_requested": frame_count,
        "centre": centre,
        "views": list(views),
        "mode": bracket.mode_string,
    }

    # ---------------------------------------------------------------
    # Preparation
    # ---------------------------------------------------------------

    t0 = time.perf_counter()
    require_ok(
        plugin._set("capturemode", "Single Shot"),
        "set capturemode Single Shot",
    )
    t1 = time.perf_counter()

    if not plugin.set_speed_blocking(centre):
        raise RuntimeError(
            f"could not set centre shutter {centre}"
        )
    t2 = time.perf_counter()

    require_ok(
        plugin._set("capturemode", bracket.mode_string),
        f"set {bracket.mode_string}",
    )
    t3 = time.perf_counter()

    # ---------------------------------------------------------------
    # Atomic PHOTO
    #
    # Timing starts immediately before bulb=1 and stops after the
    # camera has returned to idle.
    # ---------------------------------------------------------------

    longest = max(
        planner.parse_speed(view)
        for view in views
    )

    photo_start = time.perf_counter()

    press_start = photo_start
    require_ok(
        plugin._set("bulb", 1),
        "bracket press bulb=1",
    )
    press_end = time.perf_counter()

    drain_start = press_end
    frames_received = plugin._drain_frames(
        frame_count,
        longest,
    )
    drain_end = time.perf_counter()

    release_start = drain_end
    require_ok(
        plugin._set("bulb", 0),
        "bracket release bulb=0",
    )
    release_end = time.perf_counter()

    settle_start = release_end
    plugin._settle_idle()
    settle_end = time.perf_counter()

    photo_end = settle_end

    result.update({
        "frames_received": frames_received,

        "set_single_shot_ms": ms(t0, t1),
        "set_centre_shutter_ms": ms(t1, t2),
        "set_bracket_mode_ms": ms(t2, t3),
        "prepare_total_ms": ms(t0, t3),

        "press_command_ms": ms(
            press_start,
            press_end,
        ),

        "hold_until_frames_ms": ms(
            drain_start,
            drain_end,
        ),

        "release_command_ms": ms(
            release_start,
            release_end,
        ),

        "settle_idle_ms": ms(
            settle_start,
            settle_end,
        ),

        "photo_atomic_ms": ms(
            photo_start,
            photo_end,
        ),
    })

    return result


def summarize(samples):
    keys = (
        "set_single_shot_ms",
        "set_centre_shutter_ms",
        "set_bracket_mode_ms",
        "prepare_total_ms",
        "press_command_ms",
        "hold_until_frames_ms",
        "release_command_ms",
        "settle_idle_ms",
        "photo_atomic_ms",
    )

    summary = {}

    for key in keys:
        summary[key] = stats([
            sample[key]
            for sample in samples
        ])

    summary["all_frames_received"] = all(
        sample["frames_received"]
        == sample["frames_requested"]
        for sample in samples
    )

    return summary


def print_sample(index, sample):
    print(
        f"  #{index:02d}"
        f" frames={sample['frames_received']}"
        f"/{sample['frames_requested']}"
        f"  press={sample['press_command_ms']:7.1f} ms"
        f"  hold={sample['hold_until_frames_ms']:8.1f} ms"
        f"  release={sample['release_command_ms']:7.1f} ms"
        f"  settle={sample['settle_idle_ms']:7.1f} ms"
        f"  PHOTO={sample['photo_atomic_ms']:8.1f} ms"
    )


def print_summary(frame_count, summary):
    print()
    print(f"  SUMMARY {frame_count} frames")

    for key in (
        "press_command_ms",
        "hold_until_frames_ms",
        "release_command_ms",
        "settle_idle_ms",
        "photo_atomic_ms",
    ):
        item = summary[key]

        print(
            f"    {key:24s}"
            f" median={item['median_ms']:8.1f}"
            f"  p95={item['p95_ms']:8.1f}"
            f"  max={item['max_ms']:8.1f} ms"
        )

    print(
        "    all_frames_received      =",
        summary["all_frames_received"],
    )


def main():
    parser = argparse.ArgumentParser(
        description="Measure Sony native bracket timing"
    )

    parser.add_argument(
        "--n",
        type=int,
        default=3,
        help="repetitions per bracket size (default: 3)",
    )

    parser.add_argument(
        "--sizes",
        default="3,5,7,9",
        help="comma-separated bracket sizes (default: 3,5,7,9)",
    )

    parser.add_argument(
        "--iso",
        default="100",
        help="ISO used during calibration (default: 100)",
    )

    parser.add_argument(
        "--pause",
        type=float,
        default=2.0,
        help="pause between measurements in seconds (default: 2)",
    )

    parser.add_argument(
        "--output",
        default="/tmp/sony_bracket_timing.json",
        help=(
            "measurement JSON output "
            "(default: /tmp/sony_bracket_timing.json)"
        ),
    )

    args = parser.parse_args()

    if args.n <= 0:
        parser.error("--n must be > 0")

    try:
        sizes = [
            int(value.strip())
            for value in args.sizes.split(",")
            if value.strip()
        ]
    except ValueError:
        parser.error("--sizes must contain integers")

    invalid = [
        value
        for value in sizes
        if value not in BRACKETS
    ]

    if invalid:
        parser.error(
            f"unsupported bracket sizes: {invalid}"
        )

    print("=" * 72)
    print("SONY BRACKET TIMING CALIBRATION")
    print("=" * 72)
    print()
    print("WARNING: THIS COMMAND WILL TAKE REAL PHOTOGRAPHS.")
    print()
    print(
        "Measurements:",
        ", ".join(f"{size}-frame" for size in sizes),
    )
    print("Repetitions :", args.n)
    print("ISO         :", args.iso)
    print("Output      :", args.output)
    print()

    kill_desktop_camera_mounts()

    camera = None

    try:
        camera, camera_name, camera_port = detect_camera()

        plugin = SonyPlugin(
            camera,
            lambda text: print(text),
        )

        print()
        print("Initializing camera...")
        plugin.init_settings(
            iso=args.iso,
            image_format="RAW",
            white_balance="Daylight",
        )

        # Explicit neutral state before measurements.
        require_ok(
            plugin._set("capturemode", "Single Shot"),
            "initial Single Shot",
        )

        time.sleep(1.0)

        document = {
            "schema_version": 1,
            "config_type": "sony_bracket_timing_measurement",
            "measured_at_utc": (
                datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            ),
            "camera": {
                "name": camera_name,
                "port": camera_port,
            },
            "iso": str(args.iso),
            "repetitions": args.n,
            "brackets": {},
        }

        for frame_count in sizes:
            views = BRACKETS[frame_count]

            print()
            print("=" * 72)
            print(
                f"{frame_count}-FRAME BRACKET"
                f"  {views[0]} -> {views[-1]}"
                f"  centre={views[len(views)//2]}"
            )
            print("=" * 72)

            samples = []

            for index in range(1, args.n + 1):
                sample = measure_one(
                    plugin,
                    frame_count,
                    views,
                )

                samples.append(sample)
                print_sample(index, sample)

                if (
                    sample["frames_received"]
                    != frame_count
                ):
                    print(
                        "WARNING: incomplete bracket; "
                        "measurement kept in output"
                    )

                time.sleep(args.pause)

            summary = summarize(samples)

            document["brackets"][str(frame_count)] = {
                "views": views,
                "samples": samples,
                "summary": summary,
            }

            print_summary(
                frame_count,
                summary,
            )

        output = Path(args.output)
        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output.write_text(
            json.dumps(
                document,
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        print()
        print("=" * 72)
        print("DONE")
        print("Measurement file:", output)
        print("=" * 72)

    finally:
        if camera is not None:
            try:
                camera.exit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
