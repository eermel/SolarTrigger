#!/usr/bin/env python3
"""
measure_camera_wakeup.py
Version : 1.0.0
Date    : 2026-03-15

Mesure le temps de réveil caméra (exit → init → prête à déclencher).
À lancer une fois sur le Pi connecté au Nikon D850 AVANT le jour J.

Usage :
    python3 measure_camera_wakeup.py
    python3 measure_camera_wakeup.py --n 20      # 20 mesures
    python3 measure_camera_wakeup.py --full       # inclut trigger_capture
"""

import argparse
import statistics
import subprocess
import sys
import time
from datetime import datetime

try:
    import gphoto2 as gp
except ImportError:
    print("❌ gphoto2 non installé : pip install gphoto2")
    sys.exit(1)


def unmount():
    subprocess.run("killall gvfsd-gphoto2 gvfsd 2>/dev/null", shell=True,
                   stderr=subprocess.DEVNULL)
    time.sleep(0.5)


def detect_camera():
    cameras = gp.Camera.autodetect()
    if not cameras:
        return None
    name, port = cameras[0]
    al = gp.CameraAbilitiesList(); al.load()
    pl = gp.PortInfoList();         pl.load()
    cam = gp.Camera()
    cam.set_abilities(al[al.lookup_model(name)])
    cam.set_port_info(pl[pl.lookup_path(port)])
    cam.init()
    return cam


def measure_init_only(n=10):
    """Mesure exit() → detect_camera() + init()  (sans trigger)."""
    print(f"\n── Mesure init seul ({n} essais) ──")
    results = []

    # Connexion initiale
    cam = detect_camera()
    if not cam:
        print("❌ Caméra non détectée")
        return

    for i in range(n):
        # Déconnecter
        try:
            cam.exit()
        except Exception:
            pass
        time.sleep(0.2)   # laisser l'USB se libérer

        # Mesurer la reconnexion
        t0 = time.perf_counter()
        cam = detect_camera()
        t1 = time.perf_counter()

        if cam is None:
            print(f"  #{i+1:02d}  ❌ Caméra non détectée")
            continue

        elapsed = (t1 - t0) * 1000
        results.append(elapsed)
        print(f"  #{i+1:02d}  init : {elapsed:6.0f} ms")

        time.sleep(0.3)

    cam.exit()
    _print_stats("init seul", results)
    return results


def measure_init_plus_trigger(n=10):
    """Mesure exit() → init() → set_config(shutterspeed) → trigger_capture()."""
    print(f"\n── Mesure init + config + trigger ({n} essais) ──")
    results_init    = []
    results_trigger = []

    cam = detect_camera()
    if not cam:
        print("❌ Caméra non détectée")
        return

    for i in range(n):
        try:
            cam.exit()
        except Exception:
            pass
        time.sleep(0.2)

        # ── Init ──
        t0 = time.perf_counter()
        cam = detect_camera()
        t1 = time.perf_counter()

        if cam is None:
            print(f"  #{i+1:02d}  ❌ Caméra non détectée")
            continue

        init_ms = (t1 - t0) * 1000
        results_init.append(init_ms)

        # ── Config vitesse ──
        try:
            cfg  = cam.get_config()
            spd  = cfg.get_child_by_name("shutterspeed2")
            spd.set_value("1/500")
            cam.set_config(cfg)
        except Exception as e:
            print(f"  #{i+1:02d}  ⚠ config: {e}")

        # ── Trigger ──
        t2 = time.perf_counter()
        try:
            cam.trigger_capture()
        except Exception as e:
            print(f"  #{i+1:02d}  ⚠ trigger: {e}")
        t3 = time.perf_counter()

        trigger_ms = (t3 - t2) * 1000
        results_trigger.append(trigger_ms)

        total_ms = (t3 - t0) * 1000
        print(f"  #{i+1:02d}  init: {init_ms:5.0f} ms  trigger: {trigger_ms:4.0f} ms  total: {total_ms:5.0f} ms")
        time.sleep(0.5)

    cam.exit()
    _print_stats("init seul",         results_init)
    _print_stats("trigger_capture",   results_trigger)
    combined = [a + b for a, b in zip(results_init, results_trigger)]
    _print_stats("total exit→trigger", combined)

    # ── Recommandation WAKE_UP_TIME ──
    if combined:
        p95    = sorted(combined)[int(len(combined) * 0.95)]
        margin = 300   # ms de marge
        recommended = (p95 + margin) / 1000
        print(f"\n{'─'*50}")
        print(f"  ✅ WAKE_UP_TIME recommandé : {recommended:.2f} s")
        print(f"     (P95 = {p95:.0f} ms + {margin} ms de marge)")
        print(f"     À mettre dans la config du trigger.")
        print(f"{'─'*50}")
        return recommended


def _print_stats(label, values):
    if not values:
        return
    print(f"\n  {label} — statistiques sur {len(values)} mesures :")
    print(f"    min    : {min(values):6.0f} ms")
    print(f"    médiane: {statistics.median(values):6.0f} ms")
    print(f"    moyenne: {statistics.mean(values):6.0f} ms")
    print(f"    max    : {max(values):6.0f} ms")
    if len(values) >= 3:
        print(f"    stdev  : {statistics.stdev(values):6.0f} ms")
    p95 = sorted(values)[int(len(values) * 0.95)]
    print(f"    P95    : {p95:6.0f} ms")


def main():
    parser = argparse.ArgumentParser(description="Mesure temps de réveil caméra")
    parser.add_argument("--n",    type=int, default=10, help="Nombre de mesures (défaut: 10)")
    parser.add_argument("--full", action="store_true",  help="Inclure trigger_capture dans la mesure")
    args = parser.parse_args()

    print("═" * 50)
    print("  MESURE TEMPS DE RÉVEIL CAMÉRA")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═" * 50)

    unmount()

    if args.full:
        recommended = measure_init_plus_trigger(args.n)
    else:
        results = measure_init_only(args.n)
        if results:
            p95 = sorted(results)[int(len(results) * 0.95)]
            recommended = (p95 + 500) / 1000   # +500ms marge si sans trigger
            print(f"\n  ✅ WAKE_UP_TIME recommandé (sans trigger mesuré) : {recommended:.2f} s")
            print(f"     Relancer avec --full pour inclure trigger_capture.")


if __name__ == "__main__":
    main()
