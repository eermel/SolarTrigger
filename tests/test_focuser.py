#!/usr/bin/env python3
"""
test_focuser.py
Version : 1.0.00

Test autonome de l'architecture plugins focuseur.

Usage :
  python3 test_focuser.py --list
  python3 test_focuser.py --plugin zwo_eaf --status
  python3 test_focuser.py --plugin zwo_eaf --move-to 1000
  python3 test_focuser.py --plugin zwo_eaf --rel -300
  python3 test_focuser.py --plugin zwo_eaf --hold out --mode coarse --secs 2
  python3 test_focuser.py --plugin zwo_eaf --set-step 800 80 --status

ATTENTION : les mouvements font tourner le moteur. EAF branche (USB + 12V).
Placer focuser_plugins/, zwo_eaf.py et ce script cote a cote.
"""

import os, sys
# Permet de lancer ce test depuis n'importe quel dossier : ajoute la
# racine du projet (parent de tests/) au chemin d'import pour trouver plugins/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import sys
import time

from plugins.focuser import available_plugins, load_focuser


def main():
    ap = argparse.ArgumentParser(description="Test archi plugins focuseur.")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--plugin", default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--move-to", type=int, default=None)
    ap.add_argument("--rel", type=int, default=None,
                    help="deplacement relatif (+/- pas)")
    ap.add_argument("--set-step", nargs=2, type=int, default=None,
                    metavar=("COARSE", "FINE"))
    ap.add_argument("--set-max-step", type=int, default=None,
                    help="regle la limite haute logicielle (protection butee)")
    ap.add_argument("--hold", choices=("in", "out"), default=None,
                    help="maintien continu dans une direction pendant --secs")
    ap.add_argument("--mode", choices=("coarse", "fine"), default="coarse")
    ap.add_argument("--secs", type=float, default=2.0,
                    help="duree du maintien --hold (simule l'appui)")
    ap.add_argument("--interval", type=float, default=None,
                    help="pause entre pas en maintien (s) -- pour reglage")
    args = ap.parse_args()

    if args.list:
        print("Plugins focuseur disponibles :")
        for it in available_plugins():
            print(f"   {it['id']:10} -> {it['name']}")
        return

    if not args.plugin:
        ap.error("preciser --plugin <id> ou --list")

    foc = load_focuser(args.plugin, log_fn=print)
    if foc is None:
        sys.exit("Aucun plugin charge.")

    try:
        foc.connect()
        print(f"connected = {foc.connected}")

        if args.set_step:
            foc.set_step(coarse=args.set_step[0], fine=args.set_step[1])

        if args.set_max_step is not None:
            foc.set_max_step(args.set_max_step)

        if args.status:
            for k, v in foc.status().items():
                print(f"   {k:15} : {v}")

        if args.move_to is not None:
            print(f"move_to({args.move_to}) ...")
            foc.move_to(args.move_to, wait=True)
            print(f"   position = {foc.get_position()}")

        if args.rel is not None:
            print(f"move_relative({args.rel}) ...")
            foc.move_relative(args.rel, wait=True)
            print(f"   position = {foc.get_position()}")

        if args.hold:
            if args.interval is not None:
                foc.hold_interval = args.interval
                print(f"hold_interval = {foc.hold_interval}s")
            p0 = foc.get_position()
            print(f"start_continuous({args.hold}, {args.mode}) pendant "
                  f"{args.secs}s -- LE MOTEUR TOURNE")
            foc.start_continuous(args.hold, args.mode)
            time.sleep(args.secs)          # simule l'appui maintenu
            foc.stop_continuous()          # relachement
            p1 = foc.get_position()
            print(f"   position {p0} -> {p1} (delta {p1 - p0})")
    except Exception as e:
        print(f"ERREUR : {e}")
    finally:
        try:
            foc.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
