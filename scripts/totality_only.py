#!/usr/bin/env python3
"""
totality_only.py
Version : 6.4.0

Mode de secours : execute en boucle la plage d'expositions de totalité sans
prendre en compte l'heure. Le moteur ne connait aucune commande PTP propre à
une marque : CameraService choisit le CameraPlugin correspondant au boitier.
"""

import argparse
import json
import signal
import sys
import threading
import time

from services.camera_service import CameraService

_print_lock = threading.Lock()
_running = True


def _log(msg):
    with _print_lock:
        print(msg, flush=True)


def signal_handler(sig, frame):
    global _running
    _running = False


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=str, default=None)
    args = parser.parse_args()

    speeds = ["1/4000", "1/2000", "1/1000", "1/500", "1/250",
              "1/125", "1/60", "1/30", "1/15", "1/8",
              "1/4", "1/2", "1", "2", "4"]
    aperture = "f/8"
    iso = "100"

    if args.camera:
        try:
            with open(args.camera, encoding="utf-8") as f:
                cam_cfg = json.load(f)
            tot = cam_cfg.get("totality", {})
            if tot.get("speeds"):
                speeds = [str(s) for s in tot["speeds"]]
            if tot.get("aperture"):
                aperture = tot["aperture"]
            if tot.get("iso") is not None:
                iso = str(tot["iso"])
        except Exception as exc:
            _log(f"⚠ Config caméra ignorée : {exc}")

    _log("### TOTALITE UNIQUEMENT — séquence de secours")
    _log(f"Vitesses : {speeds}")
    _log(f"Ouverture : {aperture} — ISO : {iso}")

    service = CameraService(log_fn=_log)
    try:
        service.connect()
        service.init_settings(aperture=aperture, iso=iso)
    except Exception as exc:
        _log(f"Erreur init caméra/plugin : {exc}")
        service.close()
        sys.exit(1)

    cycle = 0
    try:
        while _running:
            cycle += 1
            _log(f"# Cycle totalité {cycle}")
            res = service.shoot_speed_list(speeds, photo_num_start=1)
            _log(f"Cycle {cycle} : {res.frames}/{res.planned} vues")
            if res.frames == 0:
                time.sleep(0.2)
    except Exception as exc:
        _log(f"Erreur inattendue : {exc}")
    finally:
        service.close()
        _log("✅ Totalité uniquement — terminé.")
        _log("End of the script.")


if __name__ == "__main__":
    main()
