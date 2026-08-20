#!/usr/bin/env python3
"""
test_mount.py
Version : 1.1.00

Test autonome de l'architecture plugins monture, SANS toucher au projet.

Usage :
  python3 test_mount.py --list
  python3 test_mount.py --plugin onstep --ping
  python3 test_mount.py --plugin onstep --status
  python3 test_mount.py --plugin onstep --track sidereal      # demarre suivi
  python3 test_mount.py --plugin onstep --track-off           # arrete suivi
  python3 test_mount.py --plugin onstep --move ad_right --secs 2 --speed 4
  python3 test_mount.py --plugin onstep --estop               # arret d'urgence

ATTENTION : --move fait BOUGER la monture. Verifier l'absence d'obstacle.
Placer mount_plugins/, onstep.py et ce script cote a cote.
"""

import os, sys
# Permet de lancer ce test depuis n'importe quel dossier : ajoute la
# racine du projet (parent de tests/) au chemin d'import pour trouver plugins/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import sys
import time

from plugins.mount import available_plugins, load_mount
from plugins.mount.base import (DIR_DEC_LEFT, DIR_DEC_RIGHT, DIR_AD_LEFT,
                                DIR_AD_RIGHT, RATE_SIDEREAL, RATE_SOLAR,
                                RATE_LUNAR)


def main():
    ap = argparse.ArgumentParser(description="Test archi plugins monture.")
    ap.add_argument("--list", action="store_true",
                    help="liste les plugins disponibles (aucun materiel requis)")
    ap.add_argument("--plugin", default=None, help="id du plugin (ex. onstep)")
    ap.add_argument("--port", default=None, help="port serie (override config)")
    ap.add_argument("--baudrate", type=int, default=None)
    ap.add_argument("--ping", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--track", choices=("sidereal", "solar", "lunar"),
                    default=None, help="demarre le suivi au taux donne")
    ap.add_argument("--track-off", action="store_true", help="arrete le suivi")
    ap.add_argument("--move", choices=("dec_left", "dec_right", "ad_left",
                                       "ad_right"), default=None,
                    help="bouge dans une direction pendant --secs (FAIT BOUGER)")
    ap.add_argument("--secs", type=float, default=2.0,
                    help="duree du mouvement --move (defaut 2 s)")
    ap.add_argument("--speed", type=float, default=None,
                    help="vitesse de deplacement avant --move (ex. 4)")
    ap.add_argument("--estop", action="store_true", help="arret d'urgence")
    ap.add_argument("--recenter", action="store_true",
                    help="recentrage HOME facon ASIAIR (GPS + unpark + :hC#)")
    ap.add_argument("--utc-offset", type=int, default=1,
                    help="decalage local vs UTC (France ete=2, hiver=1)")
    ap.add_argument("--gps-port", default=None, help="port GPS (auto si absent)")
    args = ap.parse_args()

    if args.list:
        print("Plugins monture disponibles :")
        for it in available_plugins():
            print(f"   {it['id']:10} -> {it['name']}")
        return

    if not args.plugin:
        ap.error("preciser --plugin <id> ou --list")

    config = {}
    if args.port:
        config["port"] = args.port
    if args.baudrate:
        config["baudrate"] = args.baudrate

    mount = load_mount(args.plugin, log_fn=print, config=config)
    if mount is None:
        sys.exit("Aucun plugin charge.")

    try:
        print("Connexion...")
        mount.connect()
        print(f"connected = {mount.connected}")

        if args.ping:
            print("ping :", mount.ping())

        if args.status:
            for k, v in mount.status().items():
                print(f"   {k:15} : {v}")

        if args.track:
            print(f"start_tracking({args.track})...")
            mount.start_tracking(args.track)
            print(f"   tracking = {mount.tracking}")

        if args.track_off:
            print("stop_tracking()...")
            mount.stop_tracking()
            print(f"   tracking = {mount.tracking}")

        if args.speed is not None:
            print(f"set_speed({args.speed})...")
            mount.set_speed(args.speed)

        if args.move:
            print(f"move({args.move}) pendant {args.secs}s "
                  f"-- LA MONTURE BOUGE")
            mount.move(args.move)
            time.sleep(args.secs)
            mount.stop()
            print("   stop() envoye")

        if args.recenter:
            print("recenter() -- GPS + unpark + retour HOME")
            print("   >>> LA MONTURE VA BOUGER, espace degage <<<")
            mount.recenter(utc_offset=args.utc_offset,
                           gps_port=args.gps_port)
            print("   HOME atteint.")

        if args.estop:
            print("emergency_stop()...")
            mount.emergency_stop()
            print("   arret d'urgence envoye")
    except Exception as e:
        print(f"ERREUR : {e}")
    finally:
        try:
            mount.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
