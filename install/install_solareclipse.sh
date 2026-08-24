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
#     5. Flask + Nginx + gunicorn/eventlet (portail web + WebSocket)
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
error()   { echo -e "${RED}[ERREUR]${NC}  $1"; exit 1; }
step()    { echo -e "\n${CYAN}══════════════════════════════════════════════════${NC}"
            echo -e "${CYAN}  $1${NC}"
            echo -e "${CYAN}══════════════════════════════════════════════════${NC}"; }

# ── Vérification root ─────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    error "Ce script doit être exécuté en tant que root : sudo ./install_solareclipse.sh"
fi

CURRENT_USER=${SUDO_USER:-$USER}
USER_HOME=$(eval echo "~$CURRENT_USER")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(dirname "$SCRIPT_DIR")"   # dossier parent = solareclipse_package/

# ── Répertoires cibles ────────────────────────────────────────────────────────
TRIGGER_DIR="$USER_HOME/python_solareclipsetrigger"   # scripts Python + jubier + sons
FLASK_DIR="$USER_HOME/flaskapp_solareclipsetrigger"   # app Flask / portail web
CONFIGS_DIR="$TRIGGER_DIR/configs"                       # configs JSON éclipse + caméra
VENV_DIR="$FLASK_DIR/venv"
JUBIER_DIR="$TRIGGER_DIR/jubier_files"
SOUNDS_DIR="$TRIGGER_DIR/Sounds"
DOMAIN="eclipse.local"
FLASK_PORT=5000

echo -e "${CYAN}"
echo "  ╔════════════════════════════════════════════════════╗"
echo "  ║   SolarEclipse — Installation Raspberry Pi v6.0 ║"
echo "  ║   Utilisateur : $CURRENT_USER"
echo "  ╚════════════════════════════════════════════════════╝"
echo -e "${NC}"
info "Répertoire package : $PACKAGE_DIR"
info "Répertoire scripts : $TRIGGER_DIR"
info "Répertoire Flask   : $FLASK_DIR"
echo ""

# ════════════════════════════════════════════════════════════
# ÉTAPE 0 — Mise à jour du système
# ════════════════════════════════════════════════════════════
step "ÉTAPE 0 — Mise à jour du système"
apt update && apt upgrade -y
apt autoremove -y
success "Système mis à jour."

# ════════════════════════════════════════════════════════════
# ÉTAPE 1 — Renommage de la machine + mDNS
# ════════════════════════════════════════════════════════════
step "ÉTAPE 1 — Nom de la machine"
DEFAULT_HOSTNAME="solareclipse"
REBOOT_NEEDED=false

read -p "Voulez-vous renommer la machine ? (y/n) [défaut: y] : " RENAME_HOSTNAME
RENAME_HOSTNAME=${RENAME_HOSTNAME:-y}
if [ "$RENAME_HOSTNAME" = "y" ]; then
    read -p "Nouveau nom [$DEFAULT_HOSTNAME] : " NEW_HOSTNAME
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
        success "Machine renommée en '$NEW_HOSTNAME'."
    else
        success "Nom '$NEW_HOSTNAME' déjà en place, pas de redémarrage nécessaire."
    fi
else
    NEW_HOSTNAME=$(hostname)
    info "Nom de la machine inchangé : $NEW_HOSTNAME"
fi

# mDNS — permet l'accès http://<hostname>.local depuis l'iPhone sans IP fixe
apt install -y avahi-daemon 2>/dev/null || true
systemctl enable avahi-daemon
systemctl start avahi-daemon
success "mDNS activé → portail accessible via http://$NEW_HOSTNAME.local"

# ════════════════════════════════════════════════════════════
# ÉTAPE 2 — Hotspot WiFi Pi (optionnel)
# ════════════════════════════════════════════════════════════
step "ÉTAPE 2 — Hotspot WiFi"
echo ""
echo -e "  ${YELLOW}Note :${NC} Si vous utilisez le hotspot de votre iPhone,"
echo -e "  le Pi s'y connecte automatiquement via NTP — pas besoin de hotspot Pi."
echo -e "  Configurez le hotspot Pi uniquement si vous travaillez SANS iPhone."
echo ""
read -p "Configurer un hotspot WiFi sur le Pi ? (y/n) [défaut: n] : " SETUP_HOTSPOT
WIFI_SSID="(non configuré)"
WIFI_PASS="(non configuré)"

if [ "$SETUP_HOTSPOT" = "y" ]; then
    # Configurer le pays WiFi
    DEFAULT_COUNTRY="FR"
    read -p "Code pays WiFi [$DEFAULT_COUNTRY] : " WIFI_COUNTRY
    WIFI_COUNTRY=${WIFI_COUNTRY:-$DEFAULT_COUNTRY}
    raspi-config nonint do_wifi_country "$WIFI_COUNTRY" 2>/dev/null || true
    iw reg set "$WIFI_COUNTRY" 2>/dev/null || true
    success "Pays WiFi configuré : $WIFI_COUNTRY"

    DEFAULT_SSID="solareclipse"
    read -p "SSID du hotspot WiFi [$DEFAULT_SSID] : " WIFI_SSID
    WIFI_SSID=${WIFI_SSID:-$DEFAULT_SSID}

    DEFAULT_PASS="solareclipse"
    while true; do
        read -p "Mot de passe WiFi [$DEFAULT_PASS] : " WIFI_PASS
        WIFI_PASS=${WIFI_PASS:-$DEFAULT_PASS}
        if [ ${#WIFI_PASS} -ge 8 ] && [ ${#WIFI_PASS} -le 63 ]; then
            break
        else
            echo -e "  ${RED}[ERREUR]${NC}  Le mot de passe WPA2 doit faire entre 8 et 63 caractères (actuel : ${#WIFI_PASS})."
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
        && success "Hotspot '$WIFI_SSID' actif (192.168.50.1)." \
        || {
            warning "hostapd non démarré au premier lancement (normal si wlan0 pas encore prête)."
            info "Redémarrez le Pi — le hotspot démarrera automatiquement au boot."
            info "Pour diagnostiquer : journalctl -u hostapd -n 30"
        }

    success "Hotspot '$WIFI_SSID' configuré (mdp: $WIFI_PASS) — IP Pi : 192.168.50.1"
else
    info "Hotspot Pi ignoré — utilisation du hotspot iPhone."
fi

# ════════════════════════════════════════════════════════════
# ÉTAPE 3 — Dépendances système
# ════════════════════════════════════════════════════════════
step "ÉTAPE 3 — Installation des dépendances système"

apt install -y \
    python3 python3-pip python3-venv \
    python3-gphoto2 python3-pygame \
    gphoto2 libgphoto2-dev \
    screen curl wget \
    gpsd gpsd-clients chrony socat \
    nginx \
    usbutils \
    psmisc procps

success "Dépendances système installées."

# ── Empêcher gvfsd de monter automatiquement la caméra (libère l'USB pour gphoto2)
info "Désactivation du montage automatique GVFS pour appareils photo..."
cat > /etc/udev/rules.d/90-camera-noautomount.rules << 'UDEVRULES'
# Empêche gvfsd-gphoto2 de prendre le contrôle des appareils photo USB
# afin que gphoto2 puisse accéder directement au device
ENV{ID_GPHOTO2}=="1", ENV{GVFS_IGNORE}="1"
UDEVRULES
udevadm control --reload-rules
success "Règle udev camera → GVFS_IGNORE=1"

# ════════════════════════════════════════════════════════════
# ÉTAPE 3b — libgphoto2 2.5.34 (support Sony A7V / ILCE-7M5)
# ════════════════════════════════════════════════════════════
# Le Sony A7V (annoncé fin 2025) n'est PAS reconnu par la libgphoto2 des dépôts
# apt (2.5.31). On compile la 2.5.34 depuis les sources officielles, installée
# dans /usr/local (prioritaire sur la version système). Sans cela, le plugin
# caméra Sony ne fonctionne pas. Les Nikon (D850...) marchent avec les deux.
step "ÉTAPE 3b — Compilation libgphoto2 depuis git (Sony A7V)"

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
    success "libgphoto2 avec support Sony A7V déjà installée dans /usr/local — étape ignorée."
else
    # Décision apt vs compilation, par NUMÉRO DE VERSION.
    # Le support du Sony A7V (ILCE-7M5) n'est présent qu'à partir d'une release
    # POSTÉRIEURE à 2.5.34 (aujourd'hui : uniquement dans le git). Donc :
    #   - si apt propose une version > 2.5.34  -> on installe via apt (simple, propre)
    #   - sinon                                -> on compile depuis git
    GPHOTO_THRESHOLD="2.5.34"   # dernière release SANS le A7V
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
        info "apt propose libgphoto2 $APT_VER (> $GPHOTO_THRESHOLD) — installation via apt (pas de compilation)."
        apt install -y libgphoto2-dev libgphoto2-6 gphoto2
        # Vérifier que le A7V est bien là dans la version apt
        APT_A7V=$(python3 -c "
import gphoto2 as gp
al = gp.CameraAbilitiesList(); al.load()
print(any('ILCE-7M5' in al.get_abilities(i).model for i in range(al.count())))
" 2>/dev/null)
        if [ "$APT_A7V" = "True" ]; then
            success "libgphoto2 $APT_VER (apt) installée — support Sony A7V CONFIRMÉ. Aucune compilation."
        else
            warning "libgphoto2 $APT_VER (apt) installée mais A7V NON détecté — bascule sur compilation git."
            USE_APT="no"
        fi
    else
        [ -n "$APT_VER" ] && info "apt propose libgphoto2 $APT_VER (≤ $GPHOTO_THRESHOLD, sans A7V) — compilation nécessaire."
    fi

    if [ "$USE_APT" = "yes" ]; then
        DO_GPHOTO="n"       # apt a suffi, on saute la compilation
    else
        read -p "Compiler libgphoto2 (git) pour le Sony A7V ? (~10-15 min) (y/n) [défaut: y] : " DO_GPHOTO
        DO_GPHOTO=${DO_GPHOTO:-y}
    fi
    if [ "$DO_GPHOTO" = "y" ]; then
        info "Installation des dépendances de compilation..."
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
        info "Clonage du dépôt git libgphoto2 (support Sony A7V)..."
        if git clone --depth 1 https://github.com/gphoto/libgphoto2.git 2>/tmp/gphoto_clone.log; then
            cd libgphoto2
            info "Génération du configure (autoreconf)..."
            autoreconf -is >/tmp/gphoto_autoreconf.log 2>&1 \
                || error "autoreconf a échoué (voir /tmp/gphoto_autoreconf.log)"
            info "Configuration..."
            ./configure --prefix=/usr/local >/tmp/gphoto_configure.log 2>&1 \
                || error "configure libgphoto2 a échoué (voir /tmp/gphoto_configure.log)"
            info "Compilation (peut prendre 10-15 min sur Pi 3B)..."
            make -j"$(nproc)" >/tmp/gphoto_make.log 2>&1 \
                || error "make libgphoto2 a échoué (voir /tmp/gphoto_make.log)"
            make install >/tmp/gphoto_install.log 2>&1 \
                || error "make install libgphoto2 a échoué (voir /tmp/gphoto_install.log)"

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
                success "libgphoto2 $NEWVER compilée — support Sony A7V (ILCE-7M5) CONFIRMÉ."
                info "Le venv Flask l'utilisera via LD_LIBRARY_PATH (configuré à l'étape 5)."
            else
                warning "libgphoto2 compilée (version '$NEWVER') mais support A7V NON confirmé."
                info "Vérifier /tmp/gphoto_*.log — les Nikon fonctionnent quand même."
            fi
        else
            warning "Clonage du dépôt git libgphoto2 échoué — Sony non supporté."
            info "Vérifiez la connexion internet (voir /tmp/gphoto_clone.log)."
        fi
        cd "$PACKAGE_DIR"
    elif [ "$USE_APT" = "yes" ]; then
        : # apt a déjà installé le support A7V ; rien à compiler (message déjà affiché)
    else
        info "Compilation libgphoto2 ignorée — le Sony A7V ne sera pas reconnu."
        info "Les boîtiers Nikon (D850...) fonctionnent avec la libgphoto2 système."
    fi
fi

# ════════════════════════════════════════════════════════════
# ÉTAPE 3c — SDK ZWO EAF (focuseur, optionnel)
# ════════════════════════════════════════════════════════════
# Le focuseur ZWO EAF utilise le SDK propriétaire ZWO (libEAFFocuser.so), qui
# ne peut être redistribué. Il doit être déposé dans vendor/eaf_sdk/ (voir le
# README de ce dossier). Absent -> le focuseur n'est pas disponible, sans bloquer
# le reste de l'installation.
step "ÉTAPE 3c — SDK ZWO EAF (focuseur, optionnel)"

EAF_VENDOR="$PACKAGE_DIR/vendor/eaf_sdk"
# Chercher la lib armv8 et la règle udev à plusieurs emplacements plausibles
EAF_LIB=$(find "$EAF_VENDOR" -path "*armv8*" -name "libEAFFocuser.so.*" 2>/dev/null | head -1)
EAF_RULES=$(find "$EAF_VENDOR" -name "eaf.rules" 2>/dev/null | head -1)

if [ -n "$EAF_LIB" ]; then
    info "SDK EAF trouvé : $EAF_LIB"
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
        success "SDK ZWO EAF installé (lib + règle udev). Débranchez/rebranchez l'EAF."
    else
        warning "libEAFFocuser non vue par ldconfig — vérifier l'installation."
    fi
else
    warning "SDK ZWO EAF absent de vendor/eaf_sdk/ — le focuseur ZWO ne sera pas disponible."
    info "Pour l'activer : déposez le SDK dans vendor/eaf_sdk/ (voir son README) puis relancez."
fi


# ════════════════════════════════════════════════════════════
# ÉTAPE 4 — Copie des scripts SolarEclipse
# ════════════════════════════════════════════════════════════
step "ÉTAPE 4 — Installation des scripts SolarEclipse"

mkdir -p "$TRIGGER_DIR" "$JUBIER_DIR" "$SOUNDS_DIR" "$CONFIGS_DIR"

# Scripts Python
if [ -d "$PACKAGE_DIR/scripts" ]; then
    cp "$PACKAGE_DIR/scripts/"*.py "$TRIGGER_DIR/"
    success "Scripts Python → $TRIGGER_DIR"
else
    warning "Dossier scripts/ introuvable dans $PACKAGE_DIR"
fi

# Dossier plugins/ (caméra, monture, focuseur) → à côté des scripts Python
# pour que eclipse_trigger.py puisse faire 'from plugins.camera import ...'
if [ -d "$PACKAGE_DIR/plugins" ]; then
    rm -rf "$TRIGGER_DIR/plugins"
    cp -r "$PACKAGE_DIR/plugins" "$TRIGGER_DIR/"
    find "$TRIGGER_DIR/plugins" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    success "Plugins (caméra/monture/focuseur) → $TRIGGER_DIR/plugins"
else
    warning "Dossier plugins/ introuvable dans $PACKAGE_DIR"
fi

# Dossier services/ (couche applicative au-dessus des plugins)
if [ -d "$PACKAGE_DIR/services" ]; then
    rm -rf "$TRIGGER_DIR/services"
    cp -r "$PACKAGE_DIR/services" "$TRIGGER_DIR/"
    find "$TRIGGER_DIR/services" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    success "Services applicatifs → $TRIGGER_DIR/services"
else
    warning "Dossier services/ introuvable dans $PACKAGE_DIR"
fi

# Backend v6 (état, orchestration GPS/trigger, runtime partagé)
if [ -d "$PACKAGE_DIR/backend" ]; then
    rm -rf "$TRIGGER_DIR/backend"
    cp -r "$PACKAGE_DIR/backend" "$TRIGGER_DIR/"
    find "$TRIGGER_DIR/backend" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    success "Backend v6 → $TRIGGER_DIR/backend"
else
    warning "Dossier backend/ introuvable dans $PACKAGE_DIR"
fi

# Dossier tests/ (harnais de diagnostic caméra/monture/focuseur)
if [ -d "$PACKAGE_DIR/tests" ]; then
    rm -rf "$TRIGGER_DIR/tests"
    cp -r "$PACKAGE_DIR/tests" "$TRIGGER_DIR/"
    chmod +x "$TRIGGER_DIR/tests/run_test.sh" 2>/dev/null || true
    success "Tests de diagnostic → $TRIGGER_DIR/tests"
fi

# Fichiers JS Jubier
if [ -d "$PACKAGE_DIR/jubier_files" ]; then
    cp "$PACKAGE_DIR/jubier_files/"* "$JUBIER_DIR/"
    success "Fichiers Jubier → $JUBIER_DIR"
else
    warning "Dossier jubier_files/ introuvable dans $PACKAGE_DIR"
fi

# Fichiers WAV
if [ -d "$PACKAGE_DIR/Sounds" ]; then
    cp "$PACKAGE_DIR/Sounds/"*.wav "$SOUNDS_DIR/"
    success "Fichiers audio → $SOUNDS_DIR"
else
    warning "Dossier Sounds/ introuvable dans $PACKAGE_DIR"
fi

# Configs JSON runtime → ~/python_solareclipsetrigger/configs/
if [ -d "$PACKAGE_DIR/configs" ]; then
    rm -rf "$CONFIGS_DIR"
    cp -a "$PACKAGE_DIR/configs" "$TRIGGER_DIR/"
    chown -R "$CURRENT_USER:$CURRENT_USER" "$CONFIGS_DIR"
    chmod 755 "$CONFIGS_DIR"
    success "Fichiers JSON de config → $CONFIGS_DIR"
else
    warning "Dossier configs/ introuvable dans $PACKAGE_DIR"
fi

# App Flask + template
if [ -f "$PACKAGE_DIR/flask_app/app.py" ]; then
    mkdir -p "$FLASK_DIR/templates"
    cp "$PACKAGE_DIR/flask_app/app.py" "$FLASK_DIR/"
    cp "$PACKAGE_DIR/flask_app/templates/index.html" "$FLASK_DIR/templates/"
    success "app.py + index.html → $FLASK_DIR"
else
    warning "flask_app/app.py introuvable dans $PACKAGE_DIR"
fi

chown -R "$CURRENT_USER:$CURRENT_USER" "$TRIGGER_DIR"

# ════════════════════════════════════════════════════════════
# ÉTAPE 5 — Flask + Nginx + gunicorn/eventlet
# ════════════════════════════════════════════════════════════
step "ÉTAPE 5 — Configuration Flask / Nginx / gunicorn"

mkdir -p "$FLASK_DIR/static/sounds" "$FLASK_DIR/templates"
chown -R "$CURRENT_USER:$CURRENT_USER" "$FLASK_DIR"

# Copier les sons dans static/ pour que Flask puisse les servir à l'iPhone
cp "$SOUNDS_DIR/"*.wav "$FLASK_DIR/static/sounds/" 2>/dev/null || true
chown "$CURRENT_USER:$CURRENT_USER" "$FLASK_DIR/static/sounds/"*.wav 2>/dev/null || true
chmod 644 "$FLASK_DIR/static/sounds/"*.wav 2>/dev/null || true
chmod 755 "$FLASK_DIR/static/sounds/" "$FLASK_DIR/static/" 2>/dev/null || true
# Nginx (www-data) doit pouvoir traverser le home directory
chmod o+x "/home/$CURRENT_USER"

# Copier socket.io.min.js dans static/ (mode offline — pas de CDN sur le Pi)
if [ -f "$PACKAGE_DIR/static/socket.io.min.js" ]; then
    cp "$PACKAGE_DIR/static/socket.io.min.js" "$FLASK_DIR/static/"
    success "socket.io.min.js → $FLASK_DIR/static/"
else
    warning "static/socket.io.min.js introuvable dans $PACKAGE_DIR — le portail utilisera la version inline de index.html"
fi

# Environnement virtuel Python
info "Création de l'environnement virtuel Python..."
sudo -u "$CURRENT_USER" HOME="$USER_HOME" python3 -m venv "$VENV_DIR"
sudo -u "$CURRENT_USER" HOME="$USER_HOME" "$VENV_DIR/bin/pip" install --upgrade pip -q
sudo -u "$CURRENT_USER" HOME="$USER_HOME" "$VENV_DIR/bin/pip" install \
    Flask \
    uWSGI \
    flask-socketio \
    eventlet \
    gphoto2 \
    pygame \
    pyserial \
    gunicorn \
    requests \
    pytz \
    timezonefinder \
    "python-socketio[client]"
success "Environnement virtuel → $VENV_DIR"

# Fichier wsgi.py
cat > "$FLASK_DIR/wsgi.py" <<EOL
from app import app, socketio

if __name__ == "__main__":
    socketio.run(app)
EOL

# uwsgi.ini (conservé pour référence / compatibilité)
cat > "$FLASK_DIR/uwsgi.ini" <<EOL
[uwsgi]
module = wsgi:app
master = true
processes = 2
socket = $FLASK_DIR/solareclipse.sock
chmod-socket = 660
vacuum = true
die-on-term = true
EOL

# Configuration Nginx — proxy vers gunicorn/eventlet (supporte WebSocket)
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
        alias $FLASK_DIR/static/;
        expires 1h;
        add_header Cache-Control "public";
    }
}
EOL

rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/solareclipse /etc/nginx/sites-enabled/
nginx -t && systemctl restart nginx
success "Nginx configuré → proxy Flask:$FLASK_PORT"

# Service systemd portal — gunicorn + eventlet (WebSocket natif)
cat > /etc/systemd/system/solareclipse.service <<EOL
[Unit]
Description=SolarEclipse Portal (Flask + SocketIO)
After=local-fs.target
Wants=local-fs.target

[Service]
User=$CURRENT_USER
Group=$CURRENT_USER
WorkingDirectory=$FLASK_DIR
Environment="PATH=$VENV_DIR/bin"
Environment="PYTHONUNBUFFERED=1"
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

# Service systemd trigger — PAS activé au boot, lancé manuellement via l'UI
# Résoudre les chemins CAMLIBS / IOLIBS réels de la libgphoto2 compilée
# (les numéros de version des sous-dossiers varient selon la release).
CAMLIBS_DIR=$(find /usr/local/lib/libgphoto2 -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort -V | tail -1)
IOLIBS_DIR=$(find /usr/local/lib/libgphoto2_port -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort -V | tail -1)
if [ -n "$CAMLIBS_DIR" ]; then
    CAMLIBS_ENV_LINE="Environment=\"CAMLIBS=$CAMLIBS_DIR\""
else
    CAMLIBS_ENV_LINE="# CAMLIBS non défini (libgphoto2 système utilisée)"
fi
if [ -n "$IOLIBS_DIR" ]; then
    IOLIBS_ENV_LINE="Environment=\"IOLIBS=$IOLIBS_DIR\""
else
    IOLIBS_ENV_LINE="# IOLIBS non défini (libgphoto2 système utilisée)"
fi

cat > /etc/systemd/system/solareclipse-trigger.service <<EOL
[Unit]
Description=SolarEclipse Trigger (déclenchement photo)
After=solareclipse.service
Wants=solareclipse.service

[Service]
User=$CURRENT_USER
WorkingDirectory=$TRIGGER_DIR
# Utiliser la libgphoto2 compilée dans /usr/local (support Sony A7V) plutôt que
# la version système. Sans ces variables, python3-gphoto2 charge l'ancienne lib
# et le Sony ILCE-7M5 n'est pas reconnu. Les chemins CAMLIBS/IOLIBS exacts sont
# résolus à l'install (versions variables) et injectés ci-dessous.
Environment="LD_LIBRARY_PATH=/usr/local/lib"
${CAMLIBS_ENV_LINE}
${IOLIBS_ENV_LINE}
ExecStartPre=/bin/test -f $TRIGGER_DIR/todayeclipse.json
ExecStart=$TRIGGER_DIR/venv/bin/python3 \
    $TRIGGER_DIR/eclipse_trigger.py \
    --file $TRIGGER_DIR/todayeclipse.json
Restart=on-failure
RestartSec=3
SuccessExitStatus=0
StandardOutput=journal
StandardError=journal
SyslogIdentifier=solareclipse-trigger
EOL

systemctl daemon-reload
systemctl enable solareclipse.service
systemctl restart solareclipse.service && success "Service solareclipse démarré/rechargé et activé au boot." \
    || warning "Service solareclipse non démarré — vérifier app.py dans $FLASK_DIR"
# Le trigger n'est PAS activé au boot — lancé depuis l'UI uniquement
success "Service solareclipse-trigger installé (lancement manuel uniquement — pas de démarrage auto)."
# Override systemd nginx : démarrer après gunicorn
mkdir -p /etc/systemd/system/nginx.service.d
cat > /etc/systemd/system/nginx.service.d/after-solareclipse.conf <<EOF
[Unit]
After=solareclipse.service
Wants=solareclipse.service
EOF
systemctl daemon-reload
# Reload nginx maintenant que gunicorn est démarré
sleep 2 && systemctl reload nginx && success "Nginx rechargé → portail actif."

# ════════════════════════════════════════════════════════════
# ÉTAPE 6 — GPS (gpsd + chrony + udev BU-353N5)
# ════════════════════════════════════════════════════════════
step "ÉTAPE 6 — Configuration GPS (GlobalSat BU-353N5)"

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
systemctl start gpsd.socket || warning "gpsd.socket non démarré."

# Configuration chrony — anti-doublon
grep -q "refclock SHM 0" /etc/chrony/chrony.conf || cat >> /etc/chrony/chrony.conf <<EOF

# ── SolarEclipse — GPS BU-353N5 comme source de temps ──
refclock SHM 0 offset 0.5 delay 0.2 refid GPS prefer
EOF

systemctl restart chronyd || warning "chronyd non redémarré."
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

success "GPS (gpsd socket + chrony + udev) configurés."
info "Sync heure GPS → portail web onglet GPS, ou : sudo python3 $TRIGGER_DIR/gps_sync.py"

# ════════════════════════════════════════════════════════════
# ÉTAPE 7 — Scripts raccourcis ~/bin/
# ════════════════════════════════════════════════════════════
step "ÉTAPE 7 — Scripts de lancement rapide"

BIN_DIR="$USER_HOME/bin"
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/sync_gps.sh" <<EOL
#!/bin/bash
echo "Synchronisation GPS BU-353N5..."
sudo python3 $TRIGGER_DIR/gps_sync.py "\$@"
EOL

cat > "$BIN_DIR/calcul_eclipse.sh" <<EOL
#!/bin/bash
# Usage : calcul_eclipse.sh --lat XX.XXX --lon YY.YYY --alt ZZZ --tz 2 --eclipse 2026-08-12
cd "$TRIGGER_DIR" || exit 1
source "$VENV_DIR/bin/activate"
python3 eclipse_calculator_py.py "\$@"
EOL

cat > "$BIN_DIR/trigger_eclipse.sh" <<EOL
#!/bin/bash
# Usage : trigger_eclipse.sh [--file todayeclipse.json]
cd "$TRIGGER_DIR" || exit 1
sudo python3 eclipse_trigger.py "\${@:---file todayeclipse.json}"
EOL

cat > "$BIN_DIR/start_portal.sh" <<EOL
#!/bin/bash
# Démarre le portail web SolarEclipse manuellement
sudo systemctl start solareclipse
echo "Portail démarré → http://$NEW_HOSTNAME.local"
EOL

cat > "$BIN_DIR/stop_portal.sh" <<EOL
#!/bin/bash
sudo systemctl stop solareclipse
echo "Portail arrêté."
EOL

chmod +x "$BIN_DIR/"*.sh
chown "$CURRENT_USER:$CURRENT_USER" "$BIN_DIR/"*.sh

# Ajouter ~/bin au PATH
PROFILE="$USER_HOME/.bashrc"
grep -q 'export PATH="$HOME/bin:$PATH"' "$PROFILE" || \
    echo 'export PATH="$HOME/bin:$PATH"' >> "$PROFILE"

success "Scripts de lancement créés dans $BIN_DIR"

# ════════════════════════════════════════════════════════════
# ÉTAPE 7b — Règle sudoers pour gps_sync (date + hwclock)
# ════════════════════════════════════════════════════════════
# gps_sync tourne en non-root (lancé par Flask) mais doit pouvoir
# modifier l'heure système via 'date' et 'hwclock'.
step "ÉTAPE 7b — Sudoers → date et hwclock sans mot de passe"

SUDOERS_FILE="/etc/sudoers.d/solareclipse"
cat > "$SUDOERS_FILE" <<EOF
# SolarEclipse — gps_sync appelle sudo date / sudo hwclock pour synchro heure
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/date
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/date
$CURRENT_USER ALL=(ALL) NOPASSWD: /sbin/hwclock
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/sbin/hwclock
# SolarEclipse — libération USB appareil photo
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/tee /sys/bus/usb/devices/*/authorized
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/pkill
EOF
chmod 440 "$SUDOERS_FILE"
visudo -c -f "$SUDOERS_FILE" && success "Règle sudoers créée : date et hwclock sans mot de passe." \
    || { warning "Syntaxe sudoers invalide — suppression." ; rm -f "$SUDOERS_FILE"; }

# ════════════════════════════════════════════════════════════
# RÉSUMÉ FINAL
# ════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Installation SolarEclipse terminée avec succès !      ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}Portail web${NC}     : ${YELLOW}http://$NEW_HOSTNAME.local${NC}"
echo -e "  ${CYAN}Hotspot WiFi${NC}    : ${YELLOW}$WIFI_SSID${NC} / ${YELLOW}$WIFI_PASS${NC}"
echo -e "  ${CYAN}Scripts Python${NC}  : ${YELLOW}$TRIGGER_DIR${NC}"
echo -e "  ${CYAN}Flask / Portail${NC} : ${YELLOW}$FLASK_DIR${NC}"
echo -e "  ${CYAN}Fichiers audio${NC}  : ${YELLOW}$SOUNDS_DIR${NC}"
echo ""
echo -e "  ${CYAN}Commandes rapides :${NC}"
echo -e "    ${YELLOW}sync_gps.sh${NC}                                  # Sync heure GPS"
echo -e "    ${YELLOW}calcul_eclipse.sh --lat X --lon Y --tz 2${NC}    # Calcul C1..C4"
echo -e "    ${YELLOW}trigger_eclipse.sh${NC}                           # Déclencher"
echo -e "    ${YELLOW}start_portal.sh${NC}  /  ${YELLOW}stop_portal.sh${NC}          # Portail web"
echo ""
echo -e "  ${YELLOW}➤  Lancer le portail : ${YELLOW}start_portal.sh${NC}"
echo ""

if [ "$REBOOT_NEEDED" = "true" ]; then
    echo -e "  ${YELLOW}⚠  Redémarrage nécessaire (renommage machine).${NC}"
    read -p "  Redémarrer maintenant ? (y/n) : " DO_REBOOT
    [ "$DO_REBOOT" = "y" ] && reboot
fi
