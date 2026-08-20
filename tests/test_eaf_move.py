#!/usr/bin/env python3
"""
test_eaf_move.py
Version : 1.0.00

Test de MOUVEMENT prudent du module zwo_eaf.py. Le moteur va tourner.
Lit la position de depart, avance d'un petit delta, revient, verifie.

Usage :
  python3 test_eaf_move.py               # test par defaut : +200 pas puis retour
  python3 test_eaf_move.py --step 500    # amplitude du test
  python3 test_eaf_move.py --abs 1000    # va a la position absolue 1000 puis revient

Placer zwo_eaf.py a cote de ce script. EAF branche (USB + 12V).
"""

import os, sys
# Permet de lancer ce test depuis n'importe quel dossier : ajoute la
# racine du projet (parent de tests/) au chemin d'import pour trouver plugins/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import time

from plugins.focuser.zwo_eaf import ZwoEaf


def main():
    ap = argparse.ArgumentParser(description="Test mouvement EAF (prudent).")
    ap.add_argument("--step", type=int, default=200,
                    help="amplitude relative du test en pas (defaut 200)")
    ap.add_argument("--abs", type=int, default=None,
                    help="tester un deplacement vers cette position absolue")
    args = ap.parse_args()

    eaf = ZwoEaf()
    info = eaf.connect()
    print(f"Connecte : {info}")
    start = eaf.get_position()
    print(f"Position de depart : {start}  (max {eaf.max_step})")

    try:
        if args.abs is not None:
            target = args.abs
            print(f"\n1) move_to({target}) ...")
            eaf.move_to(target, wait=True)
            print(f"   position = {eaf.get_position()} (attendu ~{eaf._clamp(target)})")
            print(f"\n2) retour a {start} ...")
            eaf.move_to(start, wait=True)
            print(f"   position = {eaf.get_position()}")
        else:
            d = args.step
            print(f"\n1) move_relative(+{d}) ...")
            eaf.move_relative(+d, wait=True)
            p1 = eaf.get_position()
            print(f"   position = {p1} (attendu ~{start + d})")

            time.sleep(0.5)

            print(f"\n2) move_relative(-{d}) : retour ...")
            eaf.move_relative(-d, wait=True)
            p2 = eaf.get_position()
            print(f"   position = {p2} (attendu ~{start})")

            # verification
            if abs(p2 - start) <= 2:
                print("\nOK : le focuseur est revenu a sa position de depart.")
            else:
                print(f"\nATTENTION : ecart au retour = {p2 - start} pas "
                      f"(backlash ? sens ?)")
        print(f"\nTemperature : {eaf.get_temperature()} C")
    except KeyboardInterrupt:
        print("\nInterruption -> stop()")
        eaf.stop()
    finally:
        eaf.disconnect()
        print("Deconnecte.")


if __name__ == "__main__":
    main()
