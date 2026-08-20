#!/bin/bash
# ============================================================
#   run_test.sh — lance un harnais de test avec le bon
#   environnement libgphoto2 (lib compilée dans /usr/local).
#   Version : 1.0.00
# ============================================================
#
# Les tests caméra ont besoin que python3-gphoto2 charge la libgphoto2
# compilée dans /usr/local (support Sony A7V), pas la version système.
# Ce script positionne LD_LIBRARY_PATH / CAMLIBS / IOLIBS automatiquement,
# puis lance le test demandé depuis le dossier parent (où est plugins/).
#
# Usage (depuis le dossier qui contient plugins/ ET tests/) :
#   ./tests/run_test.sh test_camera.py --detect
#   ./tests/run_test.sh test_mount.py --plugin onstep --ping
#   ./tests/run_test.sh test_focuser.py --list
#
# Les tests monture/focuseur n'ont pas besoin des variables gphoto2,
# mais le script les pose quand même (sans effet néfaste).

# Détection dynamique des chemins CAMLIBS / IOLIBS de la lib compilée.
CAMLIBS_DIR=$(find /usr/local/lib/libgphoto2 -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort -V | tail -1)
IOLIBS_DIR=$(find /usr/local/lib/libgphoto2_port -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort -V | tail -1)

export LD_LIBRARY_PATH=/usr/local/lib
[ -n "$CAMLIBS_DIR" ] && export CAMLIBS="$CAMLIBS_DIR"
[ -n "$IOLIBS_DIR" ] && export IOLIBS="$IOLIBS_DIR"

if [ -z "$1" ]; then
    echo "Usage : ./tests/run_test.sh <test_xxx.py> [arguments...]"
    echo "Exemples :"
    echo "  ./tests/run_test.sh test_camera.py --detect"
    echo "  ./tests/run_test.sh test_mount.py --plugin onstep --ping"
    echo "  ./tests/run_test.sh test_focuser.py --list"
    exit 1
fi

TEST_FILE="$1"
shift

# Lancer depuis le dossier courant (doit contenir plugins/) pour que les
# imports 'from plugins.xxx import ...' fonctionnent.
if [ ! -d "plugins" ]; then
    echo "ERREUR : ce script doit être lancé depuis le dossier qui contient plugins/"
    echo "         (par ex. ~/python_solareclipsetrigger/)"
    exit 1
fi

python3 "tests/$TEST_FILE" "$@"
