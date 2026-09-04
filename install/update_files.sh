#!/bin/bash
# ============================================================
#   SolarEclipse — Mise à jour rapide des fichiers applicatifs
#
#   Usage :
#       sudo ./install/update_files.sh
#
#   Pour toute modification de dépendances système, systemd,
#   nginx, udev, gpsd ou chrony, utiliser :
#       install/install_solareclipse.sh
# ============================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[1;36m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC}   $1"; }
info() { echo -e "${CYAN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERR]${NC}  $1"; exit 1; }


SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/datasets_sync.sh"

if [ "${SOLARECLIPSE_TEST_MODE:-0}" != "1" ] && [ "$(id -u)" -ne 0 ]; then
    err "Lancer avec sudo : sudo ./install/update_files.sh"
fi

CURRENT_USER=${SUDO_USER:-$USER}
USER_HOME=$(eval echo "~$CURRENT_USER")
PACKAGE_DIR="$(dirname "$SCRIPT_DIR")"

APP_DIR="$USER_HOME/solar-eclipse-trigger-prod"

if [ "${SOLARECLIPSE_TEST_MODE:-0}" = "1" ]; then
    PACKAGE_DIR="${SOLARECLIPSE_TEST_PACKAGE_DIR:-$PACKAGE_DIR}"
    APP_DIR="${SOLARECLIPSE_TEST_APP_DIR:-$APP_DIR}"
fi

SCRIPTS_DIR="$APP_DIR/scripts"
CONFIGS_DIR="$APP_DIR/configs"
VENV_DIR="$APP_DIR/venv"

BUILD_COMMIT=""

if command -v git >/dev/null 2>&1; then
    BUILD_COMMIT=$(git -C "$PACKAGE_DIR" rev-parse HEAD 2>/dev/null || true)
fi

if [ -z "$BUILD_COMMIT" ] && [ -f "$PACKAGE_DIR/BUILD_COMMIT" ]; then
    BUILD_COMMIT=$(tr -d '[:space:]' < "$PACKAGE_DIR/BUILD_COMMIT")
fi


echo -e "${CYAN}"
echo "  ╔════════════════════════════════════════╗"
echo "  ║   SolarEclipse — Mise à jour rapide   ║"
echo "  ╚════════════════════════════════════════╝"
echo -e "${NC}"

info "Build commit: ${BUILD_COMMIT:-unknown}"
info "Package     : $PACKAGE_DIR"
info "Application : $APP_DIR"
info "Scripts     : $SCRIPTS_DIR"
info "Configs     : $CONFIGS_DIR"
echo ""


# L'installation initiale doit déjà avoir créé la racine applicative.
[ -d "$APP_DIR" ] || \
    err "$APP_DIR absent — lancer install_solareclipse.sh d'abord"


# ── 1. Couches applicatives ──────────────────────────────────────────────────

sync_app_dir() {
    local name="$1"
    local src="$PACKAGE_DIR/$name"
    local dst="$APP_DIR/$name"

    if [ ! -d "$src" ]; then
        err "$name/ introuvable dans le package"
    fi

    rm -rf "$dst"
    cp -a "$src" "$APP_DIR/"

    find "$dst" \
        -name "__pycache__" \
        -type d \
        -exec rm -rf {} + \
        2>/dev/null || true

    ok "  $name/ → $dst"
}

info "Copie des couches applicatives..."
sync_app_dir "backend"
sync_app_dir "services"
sync_app_dir "plugins"


# ── 2. Scripts Python strictement runtime ────────────────────────────────────

info "Copie des scripts runtime..."

RUNTIME_SCRIPTS=(
    "__init__.py"
    "camera_ipc_client.py"
    "eclipse_calculator_py.py"
    "eclipse_trigger.py"
    "fanout_camera_adapter.py"
    "gps_sync.py"
)

# Valider le package complet avant de remplacer le runtime existant.
for script in "${RUNTIME_SCRIPTS[@]}"; do
    src="$PACKAGE_DIR/scripts/$script"

    if [ ! -f "$src" ]; then
        err "Script runtime manquant : $src"
    fi
done

# scripts/ appartient entièrement au runtime : aucune relique DEV ne doit survivre.
rm -rf "$SCRIPTS_DIR"
mkdir -p "$SCRIPTS_DIR"

for script in "${RUNTIME_SCRIPTS[@]}"; do
    src="$PACKAGE_DIR/scripts/$script"
    cp -a "$src" "$SCRIPTS_DIR/$script"
    ok "  $script"
done


# ── 3. Datasets éclipse ──────────────────────────────────────────────────────

info "Datasets d'éclipses..."
sync_eclipse_datasets "$PACKAGE_DIR" "$APP_DIR"


# ── 4. Flask + template ──────────────────────────────────────────────────────

info "Application Flask..."

[ -f "$PACKAGE_DIR/flask_app/app.py" ] || \
    err "flask_app/app.py absent du package"

[ -f "$PACKAGE_DIR/flask_app/templates/index.html" ] || \
    err "flask_app/templates/index.html absent du package"

mkdir -p "$APP_DIR/templates"
mkdir -p "$APP_DIR/static/sounds"

cp -a "$PACKAGE_DIR/flask_app/app.py" "$APP_DIR/app.py"
cp -a "$PACKAGE_DIR/flask_app/templates/index.html" "$APP_DIR/templates/index.html"

ok "  app.py + index.html → $APP_DIR"


# ── 5. Configurations ────────────────────────────────────────────────────────
#
# Les configurations utilisateur/runtime sont conservées.
# Seuls les profils camera_timing, immuables et livrés avec le code,
# sont rafraîchis.

info "Configurations runtime..."

mkdir -p "$CONFIGS_DIR"
mkdir -p "$CONFIGS_DIR/circumstances"
mkdir -p "$CONFIGS_DIR/camera_cfg"
mkdir -p "$CONFIGS_DIR/camera_timing"

[ -d "$PACKAGE_DIR/configs/camera_timing" ] || \
    err "configs/camera_timing/ absent du package"

cp -a "$PACKAGE_DIR/configs/camera_timing/." "$CONFIGS_DIR/camera_timing/"

ok "  camera_timing/ mis à jour"
ok "  autres configurations runtime conservées"


# ── 6. Sons ──────────────────────────────────────────────────────────────────

info "Sons WAV..."

[ -d "$PACKAGE_DIR/Sounds" ] || \
    err "Sounds/ absent du package"

mkdir -p "$APP_DIR/Sounds"
mkdir -p "$APP_DIR/static/sounds"

shopt -s nullglob
wavs=("$PACKAGE_DIR/Sounds/"*.wav)

if [ ${#wavs[@]} -eq 0 ]; then
    shopt -u nullglob
    err "Aucun fichier WAV dans $PACKAGE_DIR/Sounds"
fi

cp -a "${wavs[@]}" "$APP_DIR/Sounds/"
cp -a "${wavs[@]}" "$APP_DIR/static/sounds/"

shopt -u nullglob

ok "  WAV → $APP_DIR/Sounds"
ok "  WAV → $APP_DIR/static/sounds"


# ── 7. Métadonnée de build ──────────────────────────────────────────────────

# Migration : VERSION était un marqueur manuel et n'est plus utilisé.
rm -f "$APP_DIR/VERSION"

if [ -n "$BUILD_COMMIT" ]; then
    printf '%s\n' "$BUILD_COMMIT" > "$APP_DIR/BUILD_COMMIT"
    ok "Build commit déployé : $BUILD_COMMIT"
else
    rm -f "$APP_DIR/BUILD_COMMIT"
    warn "Build commit indéterminable."
fi


# ── 8. Droits ────────────────────────────────────────────────────────────────

if [ "${SOLARECLIPSE_TEST_MODE:-0}" != "1" ]; then
    chown -R "$CURRENT_USER:$CURRENT_USER" \
        "$APP_DIR/backend" \
        "$APP_DIR/services" \
        "$APP_DIR/plugins" \
        "$APP_DIR/scripts" \
        "$APP_DIR/templates" \
        "$APP_DIR/static" \
        "$APP_DIR/Sounds" \
        "$CONFIGS_DIR"

    if [ -f "$APP_DIR/BUILD_COMMIT" ]; then
        chown "$CURRENT_USER:$CURRENT_USER" "$APP_DIR/BUILD_COMMIT"
    fi
fi

chmod 755 "$CONFIGS_DIR"
chmod 755 "$CONFIGS_DIR/circumstances"
chmod 755 "$CONFIGS_DIR/camera_cfg"
chmod 755 "$CONFIGS_DIR/camera_timing"

ok "Droits mis à jour."


# ── 9. Validation Python minimale ────────────────────────────────────────────

info "Validation Python avant redémarrage..."

PYTHON_BIN="$VENV_DIR/bin/python3"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN=/usr/bin/python3

"$PYTHON_BIN" -m py_compile \
    "$APP_DIR/app.py" \
    "$APP_DIR/backend/trigger_service.py" \
    "$APP_DIR/services/gps_service.py" \
    "$APP_DIR/services/camera_service.py" \
    "$APP_DIR/plugins/camera/base.py" \
    "$APP_DIR/plugins/camera/sony.py" \
    "$APP_DIR/plugins/camera/nikon.py" \
    "$APP_DIR/scripts/eclipse_trigger.py" \
    "$APP_DIR/scripts/eclipse_calculator_py.py" \
    "$APP_DIR/scripts/camera_ipc_client.py" \
    "$APP_DIR/scripts/fanout_camera_adapter.py" \
    "$APP_DIR/scripts/gps_sync.py"

ok "Compilation Python minimale OK."


# ── 10. Redémarrage ─────────────────────────────────────────────────────────

if [ "${SOLARECLIPSE_SKIP_SERVICE_RESTART:-0}" = "1" ] || \
   [ "${SOLARECLIPSE_TEST_MODE:-0}" = "1" ]; then

    info "Redémarrage systemd et reload nginx ignorés."

else
    info "Redémarrage du portail SolarEclipse..."

    systemctl restart solareclipse.service || \
        err "Redémarrage solareclipse.service échoué"

    if systemctl is-active --quiet solareclipse.service; then
        ok "Portail redémarré et actif."
    else
        err "solareclipse.service n'est pas actif après redémarrage"
    fi

    if systemctl is-active --quiet nginx.service; then
        systemctl reload nginx.service 2>/dev/null || \
            warn "Reload nginx échoué"
    fi
fi


echo ""
echo -e "${GREEN}Mise à jour terminée.${NC}"
echo "Vérification :"
echo "  cat $APP_DIR/BUILD_COMMIT"
echo "  systemctl status solareclipse.service --no-pager"
