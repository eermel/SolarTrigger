#!/bin/bash
# ============================================================
#   SolarEclipse — Script d'installation complet
#   Raspberry Pi 3B / Raspberry Pi OS (Debian Bookworm)
#   Version : 6.0.0
# ============================================================
#
#   Usage :
#     chmod +x install_solareclipse.sh
#     sudo ./install_solareclipse.sh
#
#   Ce script installe et configure :
#     0. Mise à jour système
#     1. Renommage machine + mDNS (accès http://solareclipse.local)
#     2. Hotspot WiFi Pi (optionnel — désactivé si vous utilisez iPhone hotspot)
#     3. Dépendances système (Python, gphoto2, pygame, gpsd...)
#     3b. libgphoto2 2.5.34 compilée (support Sony A7V / ILCE-7M5)
#     3c. SDK ZWO EAF pour focuseur (optionnel, depuis vendor/eaf_sdk/)
#     4. Scripts SolarEclipse + backend/services/plugins
#     5. Flask + Nginx + gunicorn/gthread (portail web + WebSocket)
#     6. GPS (gpsd + chrony + service boot + udev BU-353N5)
#     7. Scripts raccourcis ~/bin/
# ============================================================

set -e

# ── Couleurs ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[1;36m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}    $1"; }
success() { echo -e "${GREEN}[OK]${NC}      $1"; }
warning() { echo -e "${YELLOW}[WARN]${NC}    $1"; }
error()   { echo -e "${RED}[ERROR]${NC}  $1"; exit 1; }
step()    { echo -e "\n${CYAN}══════════════════════════════════════════════════${NC}"
            echo -e "${CYAN}  $1${NC}"
            echo -e "${CYAN}══════════════════════════════════════════════════${NC}"; }

# ── Vérification root ─────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    error "This script must be run as root: sudo ./install_solareclipse.sh"
fi

CURRENT_USER=${SUDO_USER:-$USER}
USER_HOME=$(eval echo "~$CURRENT_USER")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(dirname "$SCRIPT_DIR")"   # dossier parent = solareclipse_package/
source "$SCRIPT_DIR/datasets_sync.sh"

BUILD_COMMIT=""

if command -v git >/dev/null 2>&1; then
    BUILD_COMMIT=$(git -C "$PACKAGE_DIR" rev-parse HEAD 2>/dev/null || true)
fi

if [ -z "$BUILD_COMMIT" ] && [ -f "$PACKAGE_DIR/BUILD_COMMIT" ]; then
    BUILD_COMMIT=$(tr -d '[:space:]' < "$PACKAGE_DIR/BUILD_COMMIT")
fi

# ── Répertoire applicatif unique ─────────────────────────────────────────────
APP_DIR="$USER_HOME/solar-eclipse-trigger-prod"
SCRIPTS_DIR="$APP_DIR/scripts"
CONFIGS_DIR="$APP_DIR/configs"
VAR_DIR="$APP_DIR/var"
VENV_DIR="$APP_DIR/venv"
SOUNDS_DIR="$APP_DIR/Sounds"

DOMAIN="eclipse.local"
FLASK_PORT=5000

echo -e "${CYAN}"
echo "  ╔════════════════════════════════════════════════════╗"
echo "  ║   SolarEclipse — Raspberry Pi Installation      ║"
echo "  ║   User: $CURRENT_USER"
echo "  ╚════════════════════════════════════════════════════╝"
echo -e "${NC}"
info "Package directory    : $PACKAGE_DIR"
info "Application directory: $APP_DIR"
echo ""

# ════════════════════════════════════════════════════════════
# STEP 0 — System update
# ════════════════════════════════════════════════════════════
step "STEP 0 — System update"
apt update && apt upgrade -y
apt autoremove -y
success "System updated."

# ════════════════════════════════════════════════════════════
# ÉTAPE 1 — Renommage de la machine + mDNS
# ════════════════════════════════════════════════════════════
step "STEP 1 — Hostname"
DEFAULT_HOSTNAME="solareclipse"
REBOOT_NEEDED=false

read -p "Rename the machine? (y/n) [default: y]: " RENAME_HOSTNAME
RENAME_HOSTNAME=${RENAME_HOSTNAME:-y}
if [ "$RENAME_HOSTNAME" = "y" ]; then
    read -p "New hostname [$DEFAULT_HOSTNAME] : " NEW_HOSTNAME
    NEW_HOSTNAME=${NEW_HOSTNAME:-$DEFAULT_HOSTNAME}
    CURRENT_HOSTNAME=$(hostname)
    if [ "$NEW_HOSTNAME" != "$CURRENT_HOSTNAME" ]; then
        hostnamectl set-hostname "$NEW_HOSTNAME"
        echo "$NEW_HOSTNAME" | tee /etc/hostname > /dev/null
        if grep -q "^127\.0\.1\.1" /etc/hosts; then
            sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$NEW_HOSTNAME/" /etc/hosts
        else
            echo -e "127.0.1.1\t$NEW_HOSTNAME" >> /etc/hosts
        fi
        REBOOT_NEEDED=true
        success "Machine renamed to '$NEW_HOSTNAME'."
    else
        success "Nom '$NEW_HOSTNAME' already set; no reboot required."
    fi
else
    NEW_HOSTNAME=$(hostname)
    info "Hostname unchanged: $NEW_HOSTNAME"
fi

# mDNS — permet l'accès http://<hostname>.local depuis l'iPhone sans IP fixe
apt install -y avahi-daemon 2>/dev/null || true
systemctl enable avahi-daemon
systemctl start avahi-daemon
success "mDNS enabled → portal available at http://$NEW_HOSTNAME.local"

# ════════════════════════════════════════════════════════════
# STEP 2 — WiFi hotspot Pi (optionnel)
# ════════════════════════════════════════════════════════════
step "STEP 2 — WiFi hotspot"
echo ""
echo -e "  ${YELLOW}Note:${NC} If you use your iPhone hotspot,"
echo -e "  the Pi connects to it automatically via NTP — no Pi hotspot is required."
echo -e "  Configure the Pi hotspot only if you work WITHOUT an iPhone."
echo ""
read -p "Configure a WiFi hotspot on the Pi? (y/n) [default: n]: " SETUP_HOTSPOT
WIFI_SSID="(not configured)"
WIFI_PASS="(not configured)"

if [ "$SETUP_HOTSPOT" = "y" ]; then
    # Configurer le pays WiFi
    DEFAULT_COUNTRY="FR"
    read -p "WiFi country code [$DEFAULT_COUNTRY] : " WIFI_COUNTRY
    WIFI_COUNTRY=${WIFI_COUNTRY:-$DEFAULT_COUNTRY}
    raspi-config nonint do_wifi_country "$WIFI_COUNTRY" 2>/dev/null || true
    iw reg set "$WIFI_COUNTRY" 2>/dev/null || true
    success "WiFi country configured: $WIFI_COUNTRY"

    DEFAULT_SSID="solareclipse"
    read -p "WiFi hotspot SSID [$DEFAULT_SSID] : " WIFI_SSID
    WIFI_SSID=${WIFI_SSID:-$DEFAULT_SSID}

    DEFAULT_PASS="solareclipse"
    while true; do
        read -p "WiFi password [$DEFAULT_PASS] : " WIFI_PASS
        WIFI_PASS=${WIFI_PASS:-$DEFAULT_PASS}
        if [ ${#WIFI_PASS} -ge 8 ] && [ ${#WIFI_PASS} -le 63 ]; then
            break
        else
            echo -e "  ${RED}[ERROR]${NC}  WPA2 password must be between 8 and 63 characters (current: ${#WIFI_PASS})."
        fi
    done

    # ── Installer hostapd + dnsmasq (méthode fiable sur Pi OS Bookworm) ────────
    # NetworkManager en mode AP bug systématiquement avec 802.1X sur wlan0.
    # hostapd + dnsmasq est la méthode native recommandée sur Raspberry Pi.
    apt install -y hostapd dnsmasq

    # Désactiver la gestion de wlan0 par NetworkManager (évite les conflits)
    cat > /etc/NetworkManager/conf.d/99-unmanaged-wlan0.conf <<EOF
[keyfile]
unmanaged-devices=interface-name:wlan0
EOF
    systemctl reload NetworkManager 2>/dev/null || true

    # Adresse IP statique pour wlan0 via systemd-networkd
    # dhcpcd n'est pas installé sur Pi OS Bookworm Lite — on utilise systemd-networkd
    cat > /etc/systemd/network/10-wlan0-static.network <<EOF
[Match]
Name=wlan0

[Network]
Address=192.168.50.1/24
ConfigureWithoutCarrier=yes
EOF
    systemctl enable systemd-networkd
    systemctl restart systemd-networkd
    # Appliquer immédiatement sans redémarrer
    ip addr flush dev wlan0 2>/dev/null || true
    ip addr add 192.168.50.1/24 dev wlan0 2>/dev/null || true
    ip link set wlan0 up 2>/dev/null || true

    # Configuration hostapd
    cat > /etc/hostapd/hostapd.conf <<EOF
interface=wlan0
driver=nl80211
ssid=$WIFI_SSID
hw_mode=g
channel=6
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=$WIFI_PASS
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
country_code=$WIFI_COUNTRY
EOF
    # Pointer hostapd vers sa config
    sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd

    # Configuration dnsmasq — DHCP sur wlan0 uniquement
    # Sauvegarder l'original si pas déjà fait
    [ -f /etc/dnsmasq.conf.orig ] || cp /etc/dnsmasq.conf /etc/dnsmasq.conf.orig
    cat > /etc/dnsmasq.d/solareclipse-hotspot.conf <<EOF
interface=wlan0
dhcp-range=192.168.50.10,192.168.50.50,255.255.255.0,24h
domain=local
address=/solareclipse.local/192.168.50.1
EOF

    # Activer et démarrer les services
    # hostapd est masqué par défaut sur Debian — unmask obligatoire avant enable
    systemctl unmask hostapd
    systemctl enable hostapd
    systemctl enable dnsmasq
    systemctl restart dnsmasq
    systemctl restart hostapd \
        && success "Hotspot '$WIFI_SSID' active (192.168.50.1)." \
        || {
            warning "hostapd did not start on first attempt (normal if wlan0 is not ready yet)."
            info "Reboot the Pi — the hotspot will start automatically at boot."
            info "For diagnostics: journalctl -u hostapd -n 30"
        }

    success "Hotspot '$WIFI_SSID' configured (password: $WIFI_PASS) — Pi IP: 192.168.50.1"
else
    info "Pi hotspot skipped — using iPhone hotspot."
fi

# ════════════════════════════════════════════════════════════
# ÉTAPE 3 — Dépendances système
# ════════════════════════════════════════════════════════════
step "STEP 3 — Install system dependencies"

apt install -y \
    python3 python3-pip python3-venv \
    gphoto2 libgphoto2-dev \
    screen curl wget \
    gpsd gpsd-clients chrony socat \
    nginx \
    usbutils \
    psmisc procps

apt install -y indi-bin indi-eqmod

success "System dependencies installed."

# L'utilisateur du service INDI doit pouvoir accéder aux périphériques série.
if id -nG "$CURRENT_USER" | grep -qw dialout; then
    success "User '$CURRENT_USER' is already a member of the dialout group."
else
    usermod -aG dialout "$CURRENT_USER"
    success "User '$CURRENT_USER' added to the dialout group."
    warning "Log out and back in to apply the new group to your session."
fi

# ── Empêcher gvfsd de monter automatiquement la caméra (libère l'USB pour gphoto2)
info "Disabling GVFS automatic camera mounting..."
cat > /etc/udev/rules.d/90-camera-noautomount.rules << 'UDEVRULES'
# Empêche gvfsd-gphoto2 de prendre le contrôle des appareils photo USB
# afin que gphoto2 puisse accéder directement au device
ENV{ID_GPHOTO2}=="1", ENV{GVFS_IGNORE}="1"
UDEVRULES
udevadm control --reload-rules
success "Camera udev rule → GVFS_IGNORE=1"

# ════════════════════════════════════════════════════════════
# ÉTAPE 3b — libgphoto2 2.5.34 (support Sony A7V / ILCE-7M5)
# ════════════════════════════════════════════════════════════
# Le Sony A7V (annoncé fin 2025) n'est PAS reconnu par la libgphoto2 des dépôts
# apt (2.5.31). On compile la 2.5.34 depuis les sources officielles, installée
# dans /usr/local (prioritaire sur la version système). Sans cela, le plugin
# caméra Sony ne fonctionne pas. Les Nikon (D850...) marchent avec les deux.
step "STEP 3b — Build libgphoto2 from git (Sony A7V)"

GPHOTO_VERSION="2.5.34"
GPHOTO_SO="/usr/local/lib/libgphoto2.so"

# On ne recompile pas si le support A7V est déjà présent dans la lib locale.
# Critère réel : le driver Sony ILCE-7M5 est-il connu par la libgphoto2 locale ?
A7V_PRESENT="False"
if [ -f /usr/local/lib/libgphoto2.so ]; then
    A7V_PRESENT=$(LD_LIBRARY_PATH=/usr/local/lib \
        CAMLIBS=$(find /usr/local/lib/libgphoto2 -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort -V | tail -1) \
        python3 -c "
import gphoto2 as gp
al = gp.CameraAbilitiesList(); al.load()
print(any('ILCE-7M5' in al.get_abilities(i).model for i in range(al.count())))
" 2>/dev/null)
fi

if [ "$A7V_PRESENT" = "True" ]; then
    success "libgphoto2 with Sony A7V support already installed in /usr/local — step skipped."
else
    # Décision apt vs compilation, par NUMÉRO DE VERSION.
    # Le support du Sony A7V (ILCE-7M5) n'est présent qu'à partir d'une release
    # POSTÉRIEURE à 2.5.34 (aujourd'hui : uniquement dans le git). Donc :
    #   - si apt propose une version > 2.5.34  -> on installe via apt (simple, propre)
    #   - sinon                                -> on compile depuis git
    GPHOTO_THRESHOLD="2.5.34"   # latest release WITHOUT A7V support
    APT_VER=$(apt-cache policy libgphoto2-dev 2>/dev/null | awk '/Candidate:/ {print $2}' \
        | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    [ -z "$APT_VER" ] && APT_VER=$(apt-cache policy libgphoto2 2>/dev/null | awk '/Candidate:/ {print $2}' \
        | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)

    USE_APT="no"
    if [ -n "$APT_VER" ]; then
        # apt convient si sa version est STRICTEMENT supérieure au seuil
        NEWEST=$(printf '%s\n%s\n' "$APT_VER" "$GPHOTO_THRESHOLD" | sort -V | tail -1)
        if [ "$APT_VER" != "$GPHOTO_THRESHOLD" ] && [ "$NEWEST" = "$APT_VER" ]; then
            USE_APT="yes"
        fi
    fi

    if [ "$USE_APT" = "yes" ]; then
        info "apt provides libgphoto2 $APT_VER (> $GPHOTO_THRESHOLD) — installation via apt (pas de compilation)."
        apt install -y libgphoto2-dev libgphoto2-6 gphoto2
        # Vérifier que le A7V est bien là dans la version apt
        APT_A7V=$(python3 -c "
import gphoto2 as gp
al = gp.CameraAbilitiesList(); al.load()
print(any('ILCE-7M5' in al.get_abilities(i).model for i in range(al.count())))
" 2>/dev/null)
        if [ "$APT_A7V" = "True" ]; then
            success "libgphoto2 $APT_VER (apt) installed — Sony A7V support CONFIRMED. No build required."
        else
            warning "libgphoto2 $APT_VER (apt) installed but A7V NOT detected — switching to git build."
            USE_APT="no"
        fi
    else
        [ -n "$APT_VER" ] && info "apt provides libgphoto2 $APT_VER (≤ $GPHOTO_THRESHOLD, without A7V) — build required."
    fi

    if [ "$USE_APT" = "yes" ]; then
        DO_GPHOTO="n"       # apt a suffi, on saute la compilation
    else
        read -p "Build libgphoto2 (git) for the Sony A7V? (~10-15 min) (y/n) [default: y]: " DO_GPHOTO
        DO_GPHOTO=${DO_GPHOTO:-y}
    fi
    if [ "$DO_GPHOTO" = "y" ]; then
        info "Installing build dependencies..."
        apt install -y \
            build-essential autoconf automake libtool pkg-config gettext \
            autopoint libltdl-dev libusb-1.0-0-dev libexif-dev libpopt-dev \
            libjpeg-dev libgd-dev git

        BUILD_DIR="/tmp/libgphoto2_build"
        rm -rf "$BUILD_DIR" && mkdir -p "$BUILD_DIR"
        cd "$BUILD_DIR"

        # IMPORTANT : le support du Sony A7V (ILCE-7M5) n'est PAS dans la release
        # tarball 2.5.34 — il a été ajouté dans le dépôt git APRÈS. On compile
        # donc depuis git (le git n'a pas de ./configure pré-généré : autoreconf).
        info "Cloning libgphoto2 git repository (Sony A7V support)..."
        if git clone --depth 1 https://github.com/gphoto/libgphoto2.git 2>/tmp/gphoto_clone.log; then
            cd libgphoto2
            info "Generating configure script (autoreconf)..."
            autoreconf -is >/tmp/gphoto_autoreconf.log 2>&1 \
                || error "autoreconf failed (see /tmp/gphoto_autoreconf.log)"
            info "Configuration..."
            ./configure --prefix=/usr/local >/tmp/gphoto_configure.log 2>&1 \
                || error "libgphoto2 configure failed (see /tmp/gphoto_configure.log)"
            info "Compilation (peut prendre 10-15 min sur Pi 3B)..."
            make -j"$(nproc)" >/tmp/gphoto_make.log 2>&1 \
                || error "libgphoto2 make failed (see /tmp/gphoto_make.log)"
            make install >/tmp/gphoto_install.log 2>&1 \
                || error "libgphoto2 make install failed (see /tmp/gphoto_install.log)"

            # Vérification : on teste la LIBRAIRIE via Python (le binding gphoto2),
            # PAS le binaire CLI /usr/local/bin/gphoto2 qui n'est pas produit par
            # la compilation de libgphoto2 (outil CLI = dépôt source séparé).
            # Le git rapporte une version type "2.5.34.1" — on vérifie surtout
            # que le driver Sony A7V (ILCE-7M5) est bien présent, c'est le but réel.
            NEWVER=$(LD_LIBRARY_PATH=/usr/local/lib python3 -c \
                "import gphoto2 as gp; print(gp.gp_library_version(0)[0])" 2>/dev/null)
            A7V_OK=$(LD_LIBRARY_PATH=/usr/local/lib \
                CAMLIBS=$(find /usr/local/lib/libgphoto2 -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort -V | tail -1) \
                IOLIBS=$(find /usr/local/lib/libgphoto2_port -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort -V | tail -1) \
                python3 -c "
import gphoto2 as gp
al = gp.CameraAbilitiesList(); al.load()
print(any('ILCE-7M5' in al.get_abilities(i).model for i in range(al.count())))
" 2>/dev/null)
            if [ "$A7V_OK" = "True" ]; then
                success "libgphoto2 $NEWVER built — Sony A7V (ILCE-7M5) support CONFIRMED."
                info "The Flask venv will use it through LD_LIBRARY_PATH (configured in step 5)."
            else
                warning "libgphoto2 built (version '$NEWVER') but A7V support is NOT confirmed."
                info "Check /tmp/gphoto_*.log — Nikon cameras still work."
            fi
        else
            warning "Failed to clone the libgphoto2 git repository — Sony is not supported."
            info "Check the internet connection (voir /tmp/gphoto_clone.log)."
        fi
        cd "$PACKAGE_DIR"
    elif [ "$USE_APT" = "yes" ]; then
        : # apt a déjà installé le support A7V ; rien à compiler (message déjà affiché)
    else
        info "libgphoto2 compilation skipped — Sony A7V will not be recognized."
        info "Nikon cameras (D850...) work with the system libgphoto2."
    fi
fi

# ════════════════════════════════════════════════════════════
# STEP 3c — ZWO EAF SDK (focuser, optional)
# ════════════════════════════════════════════════════════════
# Le focuseur ZWO EAF utilise le SDK propriétaire ZWO (libEAFFocuser.so), qui
# ne peut être redistribué. Il doit être déposé dans vendor/eaf_sdk/ (voir le
# README de ce dossier). Absent -> le focuseur n'est pas disponible, sans bloquer
# le reste de l'installation.
step "STEP 3c — ZWO EAF SDK (focuser, optional)"

EAF_VENDOR="$PACKAGE_DIR/vendor/eaf_sdk"
# Chercher la lib armv8 et la règle udev à plusieurs emplacements plausibles
EAF_LIB=$(find "$EAF_VENDOR" -path "*armv8*" -name "libEAFFocuser.so.*" 2>/dev/null | head -1)
EAF_RULES=$(find "$EAF_VENDOR" -name "eaf.rules" 2>/dev/null | head -1)

if [ -n "$EAF_LIB" ]; then
    info "EAF SDK found: $EAF_LIB"
    cp "$EAF_LIB" /usr/local/lib/
    EAF_SOname=$(basename "$EAF_LIB")            # ex: libEAFFocuser.so.1.8.1
    ln -sf "/usr/local/lib/$EAF_SOname" /usr/local/lib/libEAFFocuser.so
    # /usr/local/lib est déjà prioritaire (000-local.conf créé plus haut si Sony
    # compilé ; sinon on le crée ici aussi pour être sûr)
    [ -f /etc/ld.so.conf.d/000-local.conf ] || echo "/usr/local/lib" > /etc/ld.so.conf.d/000-local.conf
    ldconfig

    # Règle udev : accès USB à l'EAF sans root (VID 03c3, PID 1f10)
    if [ -n "$EAF_RULES" ]; then
        cp "$EAF_RULES" /etc/udev/rules.d/99-eaf.rules
    else
        # Règle par défaut si le fichier eaf.rules n'était pas fourni
        cat > /etc/udev/rules.d/99-eaf.rules << 'EAFUDEV'
# ZWO FOCUSER EAF
ACTION=="add", ATTRS{idVendor}=="03c3", ATTRS{idProduct}=="1f10", GROUP="users", MODE="0666"
EAFUDEV
    fi
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true

    if ldconfig -p | grep -q libEAFFocuser; then
        success "ZWO EAF SDK installed (library + udev rule). Unplug/replug the EAF."
    else
        warning "libEAFFocuser not visible to ldconfig — check the installation."
    fi
else
    warning "ZWO EAF SDK missing from vendor/eaf_sdk/ — the ZWO focuser will not be available."
    info "To enable it: place the SDK in vendor/eaf_sdk/ (see its README), then rerun the installer."
fi


# ════════════════════════════════════════════════════════════
# STEP 4 — Install SolarEclipse runtime
# ════════════════════════════════════════════════════════════
step "STEP 4 — Install SolarEclipse runtime"

mkdir -p "$APP_DIR"
mkdir -p "$SOUNDS_DIR"
mkdir -p "$APP_DIR/templates"
mkdir -p "$APP_DIR/static/sounds" "$APP_DIR/static/js" "$APP_DIR/static/css"

# Données mutables SolarTrigger.
# Une installation neuve doit démarrer même si var/ n'existe pas.
# Une réinstallation ne détruit jamais un var/ existant.
mkdir -p     "$VAR_DIR/state"     "$VAR_DIR/logs"     "$VAR_DIR/generated/rig"     "$VAR_DIR/generated/camera_cfg"     "$VAR_DIR/generated/circumstances"     "$VAR_DIR/generated/photo_cfg"     "$VAR_DIR/generated/exposure_opt"     "$VAR_DIR/generated/sequence"     "$VAR_DIR/generated/execution_plan"

# Scripts strictement nécessaires au runtime.
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
        error "Missing runtime script: $src"
    fi
done

# scripts/ appartient entièrement au runtime : aucune relique DEV ne doit survivre.
rm -rf "$SCRIPTS_DIR"
mkdir -p "$SCRIPTS_DIR"

for script in "${RUNTIME_SCRIPTS[@]}"; do
    src="$PACKAGE_DIR/scripts/$script"
    cp "$src" "$SCRIPTS_DIR/$script"
done
success "Runtime scripts → $SCRIPTS_DIR"

# Couches applicatives Python.
for component in backend services plugins; do
    src="$PACKAGE_DIR/$component"
    if [ ! -d "$src" ]; then
        error "Missing runtime component: $src"
    fi

    rm -rf "$APP_DIR/$component"
    cp -r "$src" "$APP_DIR/"
    find "$APP_DIR/$component"         -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
done
success "Backend / services / plugins → $APP_DIR"

# Datasets d'éclipses nécessaires au moteur Python.
sync_eclipse_datasets "$PACKAGE_DIR" "$APP_DIR"

# Sons runtime et sons servis par Flask.
if [ -d "$PACKAGE_DIR/Sounds" ]; then
    cp "$PACKAGE_DIR/Sounds/"*.wav "$SOUNDS_DIR/"
    cp "$PACKAGE_DIR/Sounds/"*.wav "$APP_DIR/static/sounds/"
    success "Audio files → $SOUNDS_DIR + $APP_DIR/static/sounds"
else
    error "Sounds/ directory not found in $PACKAGE_DIR"
fi

# Configurations PRODUIT livrées avec le package.
# configs/ ne contient aucune persistance : on peut donc le remplacer
# entièrement et supprimer d'éventuelles reliques de l'ancien layout.
if [ -d "$PACKAGE_DIR/configs" ]; then
    rm -rf "$CONFIGS_DIR"
    mkdir -p "$CONFIGS_DIR"
    cp -a "$PACKAGE_DIR/configs/." "$CONFIGS_DIR/"
    chown -R "$CURRENT_USER:$CURRENT_USER" "$CONFIGS_DIR"
    chmod 755 "$CONFIGS_DIR"
    success "Product configurations → $CONFIGS_DIR"
else
    error "configs/ directory not found in $PACKAGE_DIR"
fi

# Application Flask, template principal et assets frontend.
if [ -f "$PACKAGE_DIR/flask_app/app.py" ] && \
   [ -f "$PACKAGE_DIR/flask_app/templates/index.html" ] && \
   [ -d "$PACKAGE_DIR/flask_app/static/js" ] && \
   [ -d "$PACKAGE_DIR/flask_app/static/css" ]; then
    cp "$PACKAGE_DIR/flask_app/app.py" "$APP_DIR/app.py"
    cp "$PACKAGE_DIR/flask_app/templates/index.html" "$APP_DIR/templates/index.html"

    # Remplacement complet des assets frontend : aucune relique ancienne.
    rm -rf "$APP_DIR/static/js" "$APP_DIR/static/css"
    mkdir -p "$APP_DIR/static/js" "$APP_DIR/static/css"
    cp -a "$PACKAGE_DIR/flask_app/static/js/." "$APP_DIR/static/js/"
    cp -a "$PACKAGE_DIR/flask_app/static/css/." "$APP_DIR/static/css/"

    success "app.py + index.html + assets CSS/JS → $APP_DIR"
else
    error "Incomplete Flask runtime in $PACKAGE_DIR/flask_app"
fi

# Métadonnée logicielle unique : commit source du build.
# Migration d'une éventuelle ancienne installation.
rm -f "$APP_DIR/VERSION"

if [ -n "$BUILD_COMMIT" ]; then
    printf '%s\n' "$BUILD_COMMIT" > "$APP_DIR/BUILD_COMMIT"
    success "Build commit → $BUILD_COMMIT"
else
    rm -f "$APP_DIR/BUILD_COMMIT"
    warning "Unable to determine build commit."
fi

chown -R "$CURRENT_USER:$CURRENT_USER" "$APP_DIR"

# ════════════════════════════════════════════════════════════
# ÉTAPE 5 — Flask + Nginx + gunicorn/gthread
# ════════════════════════════════════════════════════════════
step "STEP 5 — Configure Flask / Nginx / gunicorn"

chown -R "$CURRENT_USER:$CURRENT_USER" "$APP_DIR"
chmod 644 "$APP_DIR/static/sounds/"*.wav 2>/dev/null || true
chmod 755 "$APP_DIR/static/sounds" "$APP_DIR/static" 2>/dev/null || true
# Nginx (www-data) doit pouvoir traverser le home directory
chmod o+x "/home/$CURRENT_USER"

# Environnement virtuel Python
info "Creating Python virtual environment..."
sudo -u "$CURRENT_USER" HOME="$USER_HOME" python3 -m venv "$VENV_DIR"
sudo -u "$CURRENT_USER" HOME="$USER_HOME" "$VENV_DIR/bin/pip" install --upgrade pip -q
sudo -u "$CURRENT_USER" HOME="$USER_HOME" "$VENV_DIR/bin/pip" install \
    Flask \
    flask-socketio \
    simple-websocket \
    gphoto2 \
    pygame \
    pyserial \
    gunicorn \
    pytz \
    timezonefinder
success "Virtual environment → $VENV_DIR"

# Fichier wsgi.py
cat > "$APP_DIR/wsgi.py" <<EOL
from app import app, socketio

if __name__ == "__main__":
    socketio.run(app)
EOL

# Configuration Nginx — proxy vers gunicorn/gthread (supporte WebSocket)
cat > /etc/nginx/sites-available/solareclipse <<EOL
server {
    listen 80;
    server_name $DOMAIN $NEW_HOSTNAME.local _;

    # WebSocket SocketIO
    location /socket.io/ {
        proxy_pass         http://127.0.0.1:$FLASK_PORT/socket.io/;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade \$http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host \$host;
        proxy_cache_bypass \$http_upgrade;
    }

    # Application Flask
    location / {
        proxy_pass         http://127.0.0.1:$FLASK_PORT;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }

    # Fichiers statiques (sons WAV, CSS, JS) — servis directement par Nginx
    location /static/ {
        alias $APP_DIR/static/;
        expires 1h;
        add_header Cache-Control "public";
    }
}
EOL

rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/solareclipse /etc/nginx/sites-enabled/
nginx -t && systemctl restart nginx
success "Nginx configured → proxy Flask:$FLASK_PORT"

# Résoudre les chemins CAMLIBS / IOLIBS réels de la libgphoto2 compilée.
# Le trigger étant désormais lancé par le portail, cet environnement doit
# appartenir au service principal solareclipse.service.
CAMLIBS_DIR=$(find /usr/local/lib/libgphoto2 \
    -maxdepth 1 -mindepth 1 -type d 2>/dev/null \
    | sort -V | tail -1)

IOLIBS_DIR=$(find /usr/local/lib/libgphoto2_port \
    -maxdepth 1 -mindepth 1 -type d 2>/dev/null \
    | sort -V | tail -1)

if [ -n "$CAMLIBS_DIR" ]; then
    CAMLIBS_ENV_LINE="Environment=\"CAMLIBS=$CAMLIBS_DIR\""
else
    CAMLIBS_ENV_LINE="# CAMLIBS not defined (using system libgphoto2)"
fi

if [ -n "$IOLIBS_DIR" ]; then
    IOLIBS_ENV_LINE="Environment=\"IOLIBS=$IOLIBS_DIR\""
else
    IOLIBS_ENV_LINE="# IOLIBS not defined (using system libgphoto2)"
fi

# Service systemd principal.
cat > /etc/systemd/system/solareclipse.service <<EOL
[Unit]
Description=SolarEclipse Portal
After=network.target local-fs.target indiserver-eqmod.service
Wants=network.target indiserver-eqmod.service

[Service]
Type=simple
User=$CURRENT_USER
Group=$CURRENT_USER
WorkingDirectory=$APP_DIR
Environment="PATH=$VENV_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONPATH=$APP_DIR"
Environment="LD_LIBRARY_PATH=/usr/local/lib"
${CAMLIBS_ENV_LINE}
${IOLIBS_ENV_LINE}
ExecStart=$VENV_DIR/bin/gunicorn \
    --worker-class gthread \
    --workers 1 \
    --threads 4 \
    --bind 0.0.0.0:$FLASK_PORT \
    --timeout 120 \
    wsgi:app

Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=solareclipse-portal

[Install]
WantedBy=multi-user.target
EOL

# L'ancien service trigger autonome n'existe plus dans l'architecture actuelle.
# Ces commandes rendent aussi une réinstallation propre sur une ancienne Pi.
systemctl disable --now solareclipse-trigger.service 2>/dev/null || true
rm -f /etc/systemd/system/solareclipse-trigger.service

# Service INDI pour la monture EQMod. Le groupe principal est résolu à
# l'installation ; l'accès série est fourni par l'appartenance à dialout.
RUNTIME_GROUP=$(id -gn "$CURRENT_USER")
cat > /etc/systemd/system/indiserver-eqmod.service <<EOL
[Unit]
Description=INDI server (EQMod)
After=network.target

[Service]
ExecStart=/usr/bin/indiserver indi_eqmod_telescope
Restart=on-failure
User=$CURRENT_USER
Group=$RUNTIME_GROUP

[Install]
WantedBy=multi-user.target
EOL

systemctl daemon-reload
systemctl enable indiserver-eqmod.service
systemctl start indiserver-eqmod.service
success "indiserver-eqmod service started and enabled at boot."
systemctl enable solareclipse.service
systemctl restart solareclipse.service && success "solareclipse service started/reloaded and enabled at boot." \
    || warning "solareclipse service did not start — check app.py in $APP_DIR"
# Le déclenchement photo est géré par le portail via TriggerService.
# Override systemd nginx : démarrer après gunicorn
mkdir -p /etc/systemd/system/nginx.service.d
cat > /etc/systemd/system/nginx.service.d/after-solareclipse.conf <<EOF
[Unit]
After=solareclipse.service
Wants=solareclipse.service
EOF
systemctl daemon-reload
# Reload nginx maintenant que gunicorn est démarré
sleep 2 && systemctl reload nginx && success "Nginx reloaded → portal active."

# ════════════════════════════════════════════════════════════
# ÉTAPE 6 — GPS (gpsd + chrony + udev BU-353N5)
# ════════════════════════════════════════════════════════════
step "STEP 6 — Configure GPS (GlobalSat BU-353N5)"

# Configuration gpsd — socket activation (démarré à la demande, pas au boot)
cat > /etc/default/gpsd <<EOF
START_DAEMON="true"
GPSD_OPTIONS="-n"
DEVICES="/dev/gps0"
USBAUTO="true"
GPSD_SOCKET="/var/run/gpsd.sock"
EOF

# gpsd reste socket-active et est aussi lance automatiquement par udev si le GPS est present.
systemctl disable gpsd 2>/dev/null || true
systemctl enable gpsd.socket 2>/dev/null || true
systemctl start gpsd.socket || warning "gpsd.socket did not start."

# Configuration chrony — anti-doublon
grep -q "refclock SHM 0" /etc/chrony/chrony.conf || cat >> /etc/chrony/chrony.conf <<EOF

# ── SolarEclipse — GPS BU-353N5 comme source de temps ──
refclock SHM 0 offset 0.5 delay 0.2 refid GPS prefer
EOF

systemctl restart chronyd || warning "chronyd did not restart."
systemctl enable chronyd 2>/dev/null || true

# Règle udev BU-353N5 — VID:067b PID:23a3 (Prolific PL2303)
# Crée /dev/gps0 au branchement, utilisé par gps_sync.py
cat > /etc/udev/rules.d/99-gps-bu353n5.rules <<'EOF'
# GPS SolarEclipse identifie sur ce Raspberry Pi (Prolific/ATEN 067b:23a3)
ACTION=="add", SUBSYSTEM=="tty", ATTRS{idVendor}=="067b", ATTRS{idProduct}=="23a3", \
    SYMLINK+="gps0", TAG+="systemd", ENV{SYSTEMD_WANTS}+="gpsd.service"
EOF
udevadm control --reload-rules
# Rejoue les evenements tty : si le GPS est deja present au boot/installation,
# /dev/gps0 est cree et gpsd.service est demande immediatement.
udevadm trigger --subsystem-match=tty 2>/dev/null || true

success "GPS (gpsd socket + chrony + udev) configured."
info "GPS time sync → web portal GPS tab, or: sudo $VENV_DIR/bin/python3 $SCRIPTS_DIR/gps_sync.py"

# ════════════════════════════════════════════════════════════
# ÉTAPE 7 — Scripts raccourcis ~/bin/
# ════════════════════════════════════════════════════════════
step "STEP 7 — Quick-launch scripts"

BIN_DIR="$USER_HOME/bin"
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/sync_gps.sh" <<EOL
#!/bin/bash
echo "Synchronisation GPS BU-353N5..."
sudo "$VENV_DIR/bin/python3" "$SCRIPTS_DIR/gps_sync.py" "\$@"
EOL

cat > "$BIN_DIR/calcul_eclipse.sh" <<EOL
#!/bin/bash
# Usage : calcul_eclipse.sh --lat XX.XXX --lon YY.YYY --alt ZZZ --tz 2 --eclipse 2026-08-12
"$VENV_DIR/bin/python3" "$SCRIPTS_DIR/eclipse_calculator_py.py" "\$@"
EOL

cat > "$BIN_DIR/start_portal.sh" <<EOL
#!/bin/bash
# Démarre le portail web SolarEclipse manuellement
sudo systemctl start solareclipse
echo "Portal started → http://$NEW_HOSTNAME.local"
EOL

cat > "$BIN_DIR/stop_portal.sh" <<EOL
#!/bin/bash
sudo systemctl stop solareclipse
echo "Portal stopped."
EOL

chmod +x "$BIN_DIR/"*.sh
chown "$CURRENT_USER:$CURRENT_USER" "$BIN_DIR/"*.sh

# Ajouter ~/bin au PATH
PROFILE="$USER_HOME/.bashrc"
grep -q 'export PATH="$HOME/bin:$PATH"' "$PROFILE" || \
    echo 'export PATH="$HOME/bin:$PATH"' >> "$PROFILE"

success "Launch scripts created in $BIN_DIR"

# ════════════════════════════════════════════════════════════
# ÉTAPE 7b — Règles sudoers SolarEclipse
# ════════════════════════════════════════════════════════════
# Le portail Flask tourne en non-root mais doit pouvoir :
# - synchroniser l'heure système via date / hwclock
# - libérer certains périphériques USB
# - arrêter certains processus caméra
# - rebooter la machine après effacement des données persistantes
step "STEP 7b — SolarEclipse sudoers configuration"

SUDOERS_FILE="/etc/sudoers.d/solareclipse"

cat > "$SUDOERS_FILE" <<EOF
# SolarEclipse — synchronisation heure GPS
$CURRENT_USER ALL=(root) NOPASSWD: /bin/date
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/date
$CURRENT_USER ALL=(root) NOPASSWD: /sbin/hwclock
$CURRENT_USER ALL=(root) NOPASSWD: /usr/sbin/hwclock

# SolarEclipse — libération USB appareil photo
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/tee /sys/bus/usb/devices/*/authorized
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/pkill

# SolarEclipse — reboot demandé explicitement depuis l'IHM
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/systemctl reboot
EOF

chmod 440 "$SUDOERS_FILE"

if visudo -cf "$SUDOERS_FILE"; then
    success "SolarEclipse sudoers rules installed and validated."
else
    rm -f "$SUDOERS_FILE"
    error "Invalid sudoers syntax — installation aborted."
fi

if sudo -u "$CURRENT_USER" sudo -n -l /usr/bin/systemctl reboot >/dev/null 2>&1; then
    success "Non-interactive reboot allowed for '$CURRENT_USER'."
else
    error "sudo permission for /usr/bin/systemctl reboot is not operational."
fi

# ════════════════════════════════════════════════════════════
# RÉSUMÉ FINAL
# ════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   SolarEclipse installation completed successfully!      ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}Portail web${NC}     : ${YELLOW}http://$NEW_HOSTNAME.local${NC}"
echo -e "  ${CYAN}Hotspot WiFi${NC}    : ${YELLOW}$WIFI_SSID${NC} / ${YELLOW}$WIFI_PASS${NC}"
echo -e "  ${CYAN}Application${NC}     : ${YELLOW}$APP_DIR${NC}"
echo -e "  ${CYAN}Scripts runtime${NC} : ${YELLOW}$SCRIPTS_DIR${NC}"
echo -e "  ${CYAN}Fichiers audio${NC}  : ${YELLOW}$SOUNDS_DIR${NC}"
echo ""
echo -e "  ${CYAN}Commandes rapides :${NC}"
echo -e "    ${YELLOW}sync_gps.sh${NC}                                  # GPS time sync"
echo -e "    ${YELLOW}calcul_eclipse.sh --lat X --lon Y --tz 2${NC}    # Calcul C1..C4"
echo -e "    ${YELLOW}start_portal.sh${NC}  /  ${YELLOW}stop_portal.sh${NC}          # Portail web"
echo ""
echo -e "  ${YELLOW}➤  Lancer le portail : ${YELLOW}start_portal.sh${NC}"
echo ""

if [ "$REBOOT_NEEDED" = "true" ]; then
    echo -e "  ${YELLOW}⚠  Reboot required (hostname changed).${NC}"
    read -p "  Reboot now? (y/n): " DO_REBOOT
    [ "$DO_REBOOT" = "y" ] && reboot
fi
