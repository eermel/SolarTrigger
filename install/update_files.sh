#!/bin/bash
# ============================================================
#   SolarEclipse — Mise à jour rapide des fichiers
#   Usage : sudo ./install/update_files.sh
#   Version : 6.3
#
#   Met à jour uniquement les fichiers applicatifs. Pour une
#   modification de dépendances système, systemd, nginx, udev,
#   gpsd ou chrony, utiliser install_solareclipse.sh.
# ============================================================

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[1;36m'; RED='\033[0;31m'; NC='\033[0m'
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

TRIGGER_DIR="$USER_HOME/python_solareclipsetrigger"
FLASK_DIR="$USER_HOME/flaskapp_solareclipsetrigger"
if [ "${SOLARECLIPSE_TEST_MODE:-0}" = "1" ]; then
    PACKAGE_DIR="${SOLARECLIPSE_TEST_PACKAGE_DIR:-$PACKAGE_DIR}"
    TRIGGER_DIR="${SOLARECLIPSE_TEST_TRIGGER_DIR:-$TRIGGER_DIR}"
    FLASK_DIR="${SOLARECLIPSE_TEST_FLASK_DIR:-$FLASK_DIR}"
fi
CONFIGS_DIR="$TRIGGER_DIR/configs"

PACKAGE_VERSION="unknown"
[ -f "$PACKAGE_DIR/VERSION" ] && PACKAGE_VERSION=$(tr -d '[:space:]' < "$PACKAGE_DIR/VERSION")

echo -e "${CYAN}"
echo "  ╔════════════════════════════════════════╗"
echo "  ║   SolarEclipse — Mise à jour rapide   ║"
echo "  ╚════════════════════════════════════════╝"
echo -e "${NC}"
info "Version  : $PACKAGE_VERSION"
info "Package  : $PACKAGE_DIR"
info "Scripts  : $TRIGGER_DIR"
info "Flask    : $FLASK_DIR"
info "Configs  : $CONFIGS_DIR"
echo ""

# Vérifier que l'installation initiale a été faite.
[ -d "$TRIGGER_DIR" ] || err "$TRIGGER_DIR absent — lancer install_solareclipse.sh d'abord"
[ -d "$FLASK_DIR" ]   || err "$FLASK_DIR absent — lancer install_solareclipse.sh d'abord"

# Remplace atomiquement au niveau répertoire applicatif : on ne supprime la
# destination que lorsque la source existe réellement dans le package.
sync_app_dir() {
    local name="$1"
    local src="$PACKAGE_DIR/$name"
    local dst="$TRIGGER_DIR/$name"
    if [ ! -d "$src" ]; then
        warn "$name/ introuvable dans le package — ancienne version conservée"
        return 0
    fi
    rm -rf "$dst"
    cp -a "$src" "$TRIGGER_DIR/"
    find "$dst" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    ok "  $name/ → $dst"
}

# ── 1. Scripts Python → TRIGGER_DIR
info "Copie des scripts Python..."
SCRIPTS=(
    eclipse_trigger.py
    eclipse_calculator_py.py
    gps_sync.py
    generate_debug_realistic.py
    generate_debug_total.py
    generate_debug_partial.py
    measure_camera_wakeup.py
)
for s in "${SCRIPTS[@]}"; do
    src="$PACKAGE_DIR/scripts/$s"
    if [ -f "$src" ]; then
        cp -a "$src" "$TRIGGER_DIR/"
        ok "  $s"
    else
        warn "  $s introuvable dans scripts/ — ancienne version conservée"
    fi
done

# ── 1b. Couches applicatives v6 → TRIGGER_DIR
# IMPORTANT : app.py importe backend/ et services/ depuis TRIGGER_DIR.
# Omettre ces dossiers produit un mélange de versions (bug corrigé en 6.3).
info "Copie des couches applicatives..."
sync_app_dir "backend"
sync_app_dir "services"
sync_app_dir "plugins"
sync_eclipse_datasets "$PACKAGE_DIR" "$TRIGGER_DIR"

# ── 1c. Tests / diagnostic
if [ -d "$PACKAGE_DIR/tests" ]; then
    rm -rf "$TRIGGER_DIR/tests"
    cp -a "$PACKAGE_DIR/tests" "$TRIGGER_DIR/"
    chmod +x "$TRIGGER_DIR/tests/run_test.sh" 2>/dev/null || true
    ok "  tests/ → $TRIGGER_DIR/tests"
fi

# ── 2. Flask app + template + assets statiques
info "Copie de l'application Flask..."
[ -f "$PACKAGE_DIR/flask_app/app.py" ] || err "flask_app/app.py absent du package"
[ -f "$PACKAGE_DIR/flask_app/templates/index.html" ] || err "flask_app/templates/index.html absent du package"
mkdir -p "$FLASK_DIR/templates" "$FLASK_DIR/static" "$FLASK_DIR/static/sounds"
cp -a "$PACKAGE_DIR/flask_app/app.py" "$FLASK_DIR/"
cp -a "$PACKAGE_DIR/flask_app/templates/index.html" "$FLASK_DIR/templates/"
ok "  app.py + index.html + assets → $FLASK_DIR"

# ── 3. Configs JSON runtime → TRIGGER_DIR/configs/
info "Configs JSON runtime..."
[ -d "$PACKAGE_DIR/configs" ] || err "configs/ absent du package"
mkdir -p "$CONFIGS_DIR"
mkdir -p "$CONFIGS_DIR/circumstances"
mkdir -p "$CONFIGS_DIR/camera_cfg"
cp -a "$PACKAGE_DIR/configs/." "$CONFIGS_DIR/"
find "$CONFIGS_DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
ok "  configs/ → $CONFIGS_DIR"

# ── 4. Sons WAV
info "Sons WAV..."
if [ -d "$PACKAGE_DIR/Sounds" ]; then
    mkdir -p "$TRIGGER_DIR/Sounds" "$FLASK_DIR/static/sounds"
    shopt -s nullglob
    wavs=("$PACKAGE_DIR/Sounds/"*.wav)
    if [ ${#wavs[@]} -gt 0 ]; then
        cp -a "${wavs[@]}" "$TRIGGER_DIR/Sounds/"
        cp -a "${wavs[@]}" "$FLASK_DIR/static/sounds/"
        ok "  WAV → trigger + Flask"
    fi
    shopt -u nullglob
fi

# ── 5. Fichiers Jubier
info "Fichiers Jubier..."
if [ -d "$PACKAGE_DIR/jubier_files" ]; then
    mkdir -p "$TRIGGER_DIR/jubier_files"
    cp -a "$PACKAGE_DIR/jubier_files/." "$TRIGGER_DIR/jubier_files/"
    ok "  Jubier → $TRIGGER_DIR/jubier_files"
else
    warn "  jubier_files/ absent — ancienne version conservée"
fi

# ── 6. Marqueurs de version déployée
if [ -f "$PACKAGE_DIR/VERSION" ]; then
    cp -a "$PACKAGE_DIR/VERSION" "$TRIGGER_DIR/VERSION"
    cp -a "$PACKAGE_DIR/VERSION" "$FLASK_DIR/VERSION"
    ok "Version déployée : $PACKAGE_VERSION"
fi

# ── 7. Droits
if [ "${SOLARECLIPSE_TEST_MODE:-0}" != "1" ]; then
    chown -R "$CURRENT_USER:$CURRENT_USER" "$TRIGGER_DIR" "$FLASK_DIR" "$CONFIGS_DIR"
fi
chmod 755 "$CONFIGS_DIR" "$CONFIGS_DIR/circumstances" "$CONFIGS_DIR/camera_cfg"
chmod +x "$TRIGGER_DIR/tests/run_test.sh" 2>/dev/null || true
ok "Droits mis à jour."

# ── 8. Validation minimale avant redémarrage
info "Validation Python avant redémarrage..."
PYTHON_BIN="$FLASK_DIR/venv/bin/python3"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN=/usr/bin/python3
"$PYTHON_BIN" -m py_compile \
    "$FLASK_DIR/app.py" \
    "$TRIGGER_DIR/backend/trigger_service.py" \
    "$TRIGGER_DIR/services/gps_service.py" \
    "$TRIGGER_DIR/services/camera_service.py" \
    "$TRIGGER_DIR/plugins/camera/base.py" \
    "$TRIGGER_DIR/plugins/camera/sony.py" \
    "$TRIGGER_DIR/plugins/camera/nikon.py" \
    "$TRIGGER_DIR/eclipse_trigger.py" \
    "$TRIGGER_DIR/eclipse_calculator_py.py"
ok "Compilation Python minimale OK."

# ── 9. Redémarrage Flask et contrôle de santé systemd
if [ "${SOLARECLIPSE_SKIP_SERVICE_RESTART:-0}" = "1" ] || [ "${SOLARECLIPSE_TEST_MODE:-0}" = "1" ]; then
    info "Redémarrage systemd et reload nginx ignorés."
else
    info "Redémarrage du portail Flask..."
    systemctl restart solareclipse.service || err "Redémarrage solareclipse.service échoué"
    if systemctl is-active --quiet solareclipse.service; then
        ok "Portail redémarré et actif."
    else
        err "solareclipse.service n'est pas actif après redémarrage"
    fi

    # Nginx n'a pas besoin d'être redémarré, mais un reload est sans risque si actif.
    if systemctl is-active --quiet nginx.service; then
        systemctl reload nginx.service 2>/dev/null || warn "Reload nginx échoué"
    fi
fi

echo ""
echo -e "${GREEN}✅ Mise à jour $PACKAGE_VERSION terminée.${NC}"
echo "   Vérification :"
echo "     cat $TRIGGER_DIR/VERSION"
echo "     cat $FLASK_DIR/VERSION"
echo "     systemctl status solareclipse.service --no-pager"
