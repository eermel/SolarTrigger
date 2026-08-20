#!/usr/bin/env python3
"""
camera_plugins/sony_planner.py
Version : 1.0.00

Traduit une plage de vitesses (v_max rapide -> v_min lente, pas step_il IL) en
une liste de brackets continus Sony, ou une photo unique.

Contraintes reelles du bracket Sony (toutes verifiees par EXIF sur l'A7V) :
  - bracket symetrique, nombre de vues impair parmi {3,5,7,9}, 9 au max ;
  - pas IL parmi les valeurs reelles {0.3,0.5,0.7,1.0,1.3,1.5,1.7,2.0,...} ;
  - le CENTRE et chaque vue tombent sur un cran reel de l'echelle du boitier
    (ex. 1/16 n'existe pas -> le boitier prend 1/15) ;
  - le centre doit etre un cran reel, sinon le bracket part de travers.

Politique de decoupage : PRIORITE RAPIDITE (minimiser le nombre de brackets,
donc de changements de vitesse), avec un debordement au-dela de v_min plafonne
a MAX_OVERSHOOT cran(s). Recouvrement d'un cran a la jonction accepte
(inevitable : deux brackets symetriques ne tuilent pas un total impair).
"""

import math
from itertools import product

# Echelle reelle des vitesses A7V, du plus lent au plus rapide, en secondes.
SONY_SPEEDS = [
    ("30", 30), ("25", 25), ("20", 20), ("15", 15), ("13", 13), ("10", 10),
    ("8", 8), ("6", 6), ("5", 5), ("4", 4), ("32/10", 3.2), ("25/10", 2.5),
    ("2", 2), ("16/10", 1.6), ("13/10", 1.3), ("1", 1), ("8/10", 0.8),
    ("6/10", 0.6), ("5/10", 0.5), ("4/10", 0.4), ("1/3", 1/3), ("1/4", 1/4),
    ("1/5", 1/5), ("1/6", 1/6), ("1/8", 1/8), ("1/10", 1/10), ("1/13", 1/13),
    ("1/15", 1/15), ("1/20", 1/20), ("1/25", 1/25), ("1/30", 1/30),
    ("1/40", 1/40), ("1/50", 1/50), ("1/60", 1/60), ("1/80", 1/80),
    ("1/100", 1/100), ("1/125", 1/125), ("1/160", 1/160), ("1/200", 1/200),
    ("1/250", 1/250), ("1/320", 1/320), ("1/400", 1/400), ("1/500", 1/500),
    ("1/640", 1/640), ("1/800", 1/800), ("1/1000", 1/1000), ("1/1250", 1/1250),
    ("1/1600", 1/1600), ("1/2000", 1/2000), ("1/2500", 1/2500),
    ("1/3200", 1/3200), ("1/4000", 1/4000), ("1/5000", 1/5000),
    ("1/6400", 1/6400), ("1/8000", 1/8000), ("1/10000", 1/10000),
    ("1/12800", 1/12800), ("1/16000", 1/16000),
]

SONY_BRACKET_STEPS = [0.3, 0.5, 0.7, 1.0, 1.3, 1.5, 1.7, 2.0, 2.3, 2.5, 2.7, 3.0]
SIZES = [9, 7, 5, 3]
MAX_OVERSHOOT = 1     # crans de debordement autorises au-dela de v_min


class Bracket:
    """Un bracket continu a executer. mode_string est la chaine capturemode
    Sony correspondante (ex. 'Continuous Bracket 1.0 EV 9 Img.')."""
    def __init__(self, centre, step, nimg, views):
        self.centre = centre        # chaine vitesse du centre (ex. '1/250')
        self.step = step            # pas IL reel (ex. 1.0)
        self.nimg = nimg            # nb de vues (3/5/7/9)
        self.views = views          # liste des vitesses reelles (info/log)

    @property
    def mode_string(self):
        # format Sony : 'Continuous Bracket <step> EV <nimg> Img.'
        return f"Continuous Bracket {self.step:.1f} EV {self.nimg} Img."

    def __repr__(self):
        return f"<Bracket centre={self.centre} {self.mode_string} {self.views}>"


class SinglePhoto:
    """Une seule vue a une vitesse donnee (cas v_max == v_min)."""
    def __init__(self, speed):
        self.speed = speed

    def __repr__(self):
        return f"<SinglePhoto {self.speed}>"


def _ev(sec):
    return math.log2(sec)


def _snap_step(step_il):
    return min(SONY_BRACKET_STEPS, key=lambda s: abs(s - step_il))


def _snap_speed_by_ev(target_ev):
    """Cran reel le plus proche en EV. Retourne (chaine, sec, ev)."""
    b = min(SONY_SPEEDS, key=lambda x: abs(_ev(x[1]) - target_ev))
    return (b[0], b[1], _ev(b[1]))


def parse_speed(s):
    """'1/500' -> 0.002 ; '5/10' -> 0.5 ; '4' -> 4.0."""
    s = str(s).strip()
    if "/" in s:
        a, b = s.split("/")
        return float(a) / float(b)
    return float(s)


def _best_composition(n_frames):
    """Choisit les tailles de brackets. PRIORITE RAPIDITE : minimise d'abord le
    NOMBRE de brackets, puis le debordement (<= MAX_OVERSHOOT), gros d'abord.
    Retourne [] si n_frames <= 1 (photo unique)."""
    if n_frames <= 1:
        return []
    # 1) sous le plafond de debordement
    for k in range(1, n_frames // 3 + 3):
        best = None
        seen = set()
        for combo in product(SIZES, repeat=k):
            s = tuple(sorted(combo, reverse=True))
            if s in seen:
                continue
            seen.add(s)
            tot = sum(s)
            ov = tot - n_frames
            if ov < 0 or ov > MAX_OVERSHOOT:
                continue
            key = (ov, [-x for x in s])
            if best is None or key < best[0]:
                best = (key, list(s))
        if best:
            return best[1]
    # 2) aucune solution sous le plafond : relacher le plafond
    for k in range(1, n_frames // 3 + 3):
        best = None
        for combo in product(SIZES, repeat=k):
            s = tuple(sorted(combo, reverse=True))
            tot = sum(s)
            ov = tot - n_frames
            if ov < 0:
                continue
            key = (ov, [-x for x in s])
            if best is None or key < best[0]:
                best = (key, list(s))
        if best:
            return best[1]
    return [3]


def plan(v_max, v_min, step_il):
    """Retourne (step_reel, n_frames, sequence) ou sequence est une liste de
    Bracket et/ou d'un SinglePhoto. v_max = plus rapide, v_min = plus lente.
    Accepte des chaines ('1/4000') ou des secondes."""
    vmax_s = parse_speed(v_max) if isinstance(v_max, str) else v_max
    vmin_s = parse_speed(v_min) if isinstance(v_min, str) else v_min
    # garantir vmax = rapide (petit temps), vmin = lent (grand temps)
    if vmax_s > vmin_s:
        vmax_s, vmin_s = vmin_s, vmax_s

    step = _snap_step(step_il)
    ev_fast = _ev(vmax_s)
    ev_slow = _ev(vmin_s)
    n_frames = round((ev_slow - ev_fast) / step) + 1
    if n_frames < 1:
        n_frames = 1

    if n_frames == 1:
        c_str, _, _ = _snap_speed_by_ev(ev_fast)
        return step, 1, [SinglePhoto(c_str)]

    sizes = _best_composition(n_frames)
    seq = []
    pos = ev_fast
    for nimg in sizes:
        half = (nimg - 1) // 2
        c_str, _, c_ev = _snap_speed_by_ev(pos + half * step)
        views = []
        prev = None
        for k in range(-half, half + 1):
            s, _, _ = _snap_speed_by_ev(c_ev + k * step)
            if s != prev:
                views.append(s)
            prev = s
        seq.append(Bracket(c_str, step, nimg, views))
        pos += nimg * step
    return step, n_frames, seq


# --------------------------------------------------------------------------- #
# Auto-test (python3 sony_planner.py)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    cases = [
        ("Egypte totalite 1 IL", "1/4000", "4", 1.0),
        ("Step 0.5 IL", "1/4000", "4", 0.5),
        ("Diamond ring", "1/4000", "1/250", 1.0),
        ("Plage courte", "1/4000", "1/15", 1.0),
        ("Une seule vitesse", "1/1000", "1/1000", 1.0),
        ("Partielle 2 vues", "1/2000", "1/1000", 1.0),
    ]
    for label, vmax, vmin, st in cases:
        step, nf, seq = plan(vmax, vmin, st)
        print(f"\n### {label} ({vmax}->{vmin}, {st} IL) | step {step} | {nf} vues")
        for item in seq:
            if isinstance(item, SinglePhoto):
                print(f"   PHOTO {item.speed}")
            else:
                print(f"   {item.mode_string} centre {item.centre} -> {item.views}")
