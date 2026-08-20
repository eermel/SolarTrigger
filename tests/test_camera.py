#!/usr/bin/env python3
"""
test_camera.py
Version : 1.0.00

Test autonome de l'architecture plugins camera, par ETAPES isolees (comme
test_mount.py / test_focuser.py). Le plugin est choisi AUTOMATIQUEMENT selon
le boitier branche (Sony -> bracket interne, Nikon D850 -> photo-par-photo,
Nikon Z -> photo-par-photo + viewfinder).

Usage :
  python3 test_camera.py --detect                      # quel plugin, sans rien declencher
  python3 test_camera.py --plan 1/4000 4 1.0           # plan seul (Sony), SANS camera
  python3 test_camera.py --init --iso 100              # applique l'init et s'arrete
  python3 test_camera.py --single 1/500                # UNE photo a 1/500
  python3 test_camera.py --seq 1/4000 4 1.0 --iso 100  # une sequence complete
  python3 test_camera.py --config shutterspeed iso imagequality   # lit des configs

ATTENTION : --single et --seq DECLENCHENT des photos. Carte memoire en place.
Lancer depuis le dossier qui contient plugins/ .
"""

import os, sys
# Permet de lancer ce test depuis n'importe quel dossier : ajoute la
# racine du projet (parent de tests/) au chemin d'import pour trouver plugins/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import sys

from plugins.camera import load_plugin, get_camera_model
from plugins.camera import sony_planner as planner


def _open_camera():
    try:
        import gphoto2 as gp
    except ImportError:
        sys.exit("python3-gphoto2 requis pour dialoguer avec le boitier.")
    try:
        cam = gp.Camera()
        cam.init()
        return cam, gp
    except gp.GPhoto2Error as e:
        sys.exit(f"Init camera impossible : {e}")


def _read_config(cam, gp, names):
    """Lit et affiche des configs par nom (diagnostic init/boitier)."""
    for name in names:
        try:
            cfg = cam.get_config()
            w = cfg.get_child_by_name(name)
            print(f"   {name:16} = {w.get_value()}")
        except gp.GPhoto2Error:
            print(f"   {name:16} = (absent sur ce boitier)")


def main():
    ap = argparse.ArgumentParser(description="Test archi plugins camera.")
    ap.add_argument("--detect", action="store_true",
                    help="detecte le boitier et le plugin, sans rien declencher")
    ap.add_argument("--plan", nargs=3, metavar=("VMAX", "VMIN", "STEP"),
                    default=None, help="affiche le plan de brackets (sans camera)")
    ap.add_argument("--init", action="store_true",
                    help="applique le bloc d'init puis s'arrete")
    ap.add_argument("--single", metavar="SPEED", default=None,
                    help="prend UNE photo a la vitesse donnee")
    ap.add_argument("--seq", nargs=3, metavar=("VMAX", "VMIN", "STEP"),
                    default=None, help="execute une sequence shoot_speeds")
    ap.add_argument("--config", nargs="+", default=None,
                    help="lit et affiche des configs (ex: shutterspeed iso)")
    ap.add_argument("--iso", default=None)
    ap.add_argument("--aperture", default=None)
    args = ap.parse_args()

    # --- plan seul : ne necessite pas de camera --------------------------- #
    if args.plan:
        vmax, vmin, step = args.plan[0], args.plan[1], float(args.plan[2])
        s, nf, seq = planner.plan(vmax, vmin, step)
        print(f"PLAN {vmax}->{vmin} @ {step} IL : step {s}, {nf} vues")
        for item in seq:
            if isinstance(item, planner.SinglePhoto):
                print(f"   PHOTO {item.speed}")
            else:
                print(f"   {item.mode_string} centre {item.centre} "
                      f"-> {item.views}")
        return

    # --- toutes les autres etapes necessitent le boitier ------------------ #
    cam, gp = _open_camera()
    plugin = None
    try:
        model = get_camera_model(cam)
        print(f"Boitier detecte : '{model}'")
        plugin = load_plugin(cam, log_fn=print)
        if plugin is None:
            sys.exit("Aucun plugin ne correspond a ce boitier.")
        print(f"Plugin : {plugin.name}")

        if args.detect:
            return                      # detection seule, rien d'autre

        if args.config:
            print("Configs lues :")
            _read_config(cam, gp, args.config)

        if args.init or args.single or args.seq:
            print("--- init reglages ---")
            plugin.init_settings(aperture=args.aperture, iso=args.iso)

        if args.init and not (args.single or args.seq):
            print("Init appliquee. (Verifier avec --config au besoin.)")

        if args.single:
            print(f"--- photo unique {args.single} ---")
            res = plugin.shoot_single(args.single)
            print(f"RESULTAT : {res.frames}/{res.planned} ({res.detail})")

        if args.seq:
            vmax, vmin, step = args.seq[0], args.seq[1], float(args.seq[2])
            print(f"--- sequence {vmax}->{vmin} @ {step} IL ---")
            res = plugin.shoot_speeds(vmax, vmin, step)
            print(f"RESULTAT : {res.frames}/{res.planned} ({res.detail})")
    except Exception as e:
        print(f"ERREUR : {e}")
    finally:
        # securite : si plugin Sony, relacher obturateur / revenir single shot
        try:
            if plugin is not None and plugin.name == "sony":
                plugin._set("bulb", 0)
                plugin._set("capturemode", "Single Shot")
        except Exception:
            pass
        cam.exit()


if __name__ == "__main__":
    main()
