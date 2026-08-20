#!/usr/bin/env python3

"""
gps_sync_bu353n5.py
───────────────────
Version : 5.1.02
Date    : 2026-03-15

Détecte automatiquement le GPS USB GlobalSat BU-353N5,
lit les trames NMEA, et synchronise l'heure système du Raspberry Pi.

Usage :
    sudo python3 gps_sync_bu353n5.py
    sudo python3 gps_sync_bu353n5.py --timeout 120
    sudo python3 gps_sync_bu353n5.py --port /dev/ttyUSB0 --verbose
    sudo python3 gps_sync_bu353n5.py --dry-run

Dépendances :
    pip3 install pyserial --break-system-packages
"""

import argparse
import glob
import logging
import os
import re
import subprocess
import sys
import time
import serial
from datetime import datetime, timezone

# ─── Couleurs ANSI ────────────────────────────────────────────────────────────
class Colors:
    RED    = "\033[1;31m"
    GREEN  = "\033[1;32m"
    YELLOW = "\033[1;33m"
    BLUE   = "\033[1;34m"
    ORANGE = "\033[38;2;255;127;0m"
    CYAN   = "\033[1;36m"
    RESET  = "\033[0m"

# ─── Constantes BU-353N5 ──────────────────────────────────────────────────────
BU353N5_VENDOR_ID  = "067b"   # Prolific Technology (PL2303)
BU353N5_PRODUCT_ID = "23a3"
BAUD_RATE          = 4800     # Vitesse série du BU-353N5
READ_TIMEOUT       = 2        # secondes timeout lecture série

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)

# ──────────────────────────────────────────────────────────────────────────────
# DÉTECTION DU PORT USB
# ──────────────────────────────────────────────────────────────────────────────

def find_bu353n5_port():
    """
    Détecte automatiquement le port série du BU-353N5 en cherchant
    le Vendor ID / Product ID Prolific PL2303 dans /sys/bus/usb.
    Retourne le chemin du port (ex: /dev/ttyUSB0) ou None.
    """
    logging.info(f"{Colors.BLUE}Recherche du GPS (VID:{BU353N5_VENDOR_ID} PID:{BU353N5_PRODUCT_ID})...{Colors.RESET}")

    # Lien udev stable cree par install_solareclipse.sh.
    if os.path.exists("/dev/gps0"):
        logging.info(f"{Colors.GREEN}GPS détecté via lien stable : /dev/gps0{Colors.RESET}")
        return "/dev/gps0"

    # Parcourir les devices USB ttyUSB*
    for tty_path in sorted(glob.glob("/sys/bus/usb-serial/devices/ttyUSB*")):
        try:
            # Remonter jusqu'au device USB parent
            real_path = os.path.realpath(tty_path)
            usb_device_path = real_path

            # Chercher idVendor / idProduct en remontant l'arborescence
            for _ in range(6):
                usb_device_path = os.path.dirname(usb_device_path)
                vid_file = os.path.join(usb_device_path, "idVendor")
                pid_file = os.path.join(usb_device_path, "idProduct")
                if os.path.exists(vid_file) and os.path.exists(pid_file):
                    with open(vid_file) as vf, open(pid_file) as pf:
                        vid = vf.read().strip()
                        pid = pf.read().strip()
                    if vid == BU353N5_VENDOR_ID and pid == BU353N5_PRODUCT_ID:
                        tty_name = os.path.basename(tty_path)
                        port = f"/dev/{tty_name}"
                        logging.info(f"{Colors.GREEN}GPS BU-353N5 détecté sur : {port}{Colors.RESET}")
                        return port
                    break  # VID/PID trouvés mais ne correspondent pas
        except Exception:
            continue

    # Pas de fallback vers un ttyUSB arbitraire : il pourrait s'agir de la monture.
    return None

# ──────────────────────────────────────────────────────────────────────────────
# PARSING NMEA
# ──────────────────────────────────────────────────────────────────────────────

def nmea_checksum_valid(sentence):
    """Vérifie le checksum XOR d'une trame NMEA."""
    try:
        if "*" not in sentence:
            return False
        data, checksum = sentence.strip().lstrip("$").split("*")
        calc = 0
        for c in data:
            calc ^= ord(c)
        return calc == int(checksum, 16)
    except Exception:
        return False

def is_rmc_void(sentence):
    """Retourne True si c'est une trame RMC avec status V (pas de fix) — sert à resetter la séquence."""
    try:
        if not (sentence.startswith("$GPRMC") or sentence.startswith("$GNRMC")):
            return False
        parts = sentence.split(",")
        return len(parts) >= 3 and parts[2] == "V"
    except Exception:
        return False

def parse_gprmc(sentence):
    """
    Parse une trame $GPRMC ou $GNRMC et retourne (datetime_utc, latitude, longitude, vitesse_kt)
    ou None si invalide / pas de fix.

    Format : $GPRMC,HHMMSS.ss,A,LLLL.LL,a,YYYYY.YY,a,x.x,x.x,DDMMYY,x.x,a*hh
    $GNRMC = même format, préfixe GNSS multi-constellation (BU-353N5 moderne)
    """
    try:
        if not (sentence.startswith("$GPRMC") or sentence.startswith("$GNRMC")):
            return None
        if not nmea_checksum_valid(sentence):
            logging.debug("Checksum GPRMC invalide")
            return None

        parts = sentence.split(",")
        if len(parts) < 10:
            return None

        status = parts[2]   # A = valid, V = void
        if status != "A":
            return None     # Pas de fix GPS

        time_str = parts[1][:6]   # HHMMSS
        date_str = parts[9]       # DDMMYY

        hour   = int(time_str[0:2])
        minute = int(time_str[2:4])
        second = int(time_str[4:6])
        day    = int(date_str[0:2])
        month  = int(date_str[2:4])
        year   = 2000 + int(date_str[4:6])

        dt_utc = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)

        # Latitude
        lat_raw = parts[3]
        lat_dir = parts[4]
        lat_deg = float(lat_raw[:2]) + float(lat_raw[2:]) / 60
        if lat_dir == "S":
            lat_deg = -lat_deg

        # Longitude
        lon_raw = parts[5]
        lon_dir = parts[6]
        lon_deg = float(lon_raw[:3]) + float(lon_raw[3:]) / 60
        if lon_dir == "W":
            lon_deg = -lon_deg

        speed_kt = float(parts[7]) if parts[7] else 0.0

        return dt_utc, lat_deg, lon_deg, speed_kt

    except Exception as e:
        logging.debug(f"Erreur parsing GPRMC : {e}")
        return None

def parse_gpgga(sentence):
    """
    Parse une trame $GPGGA ou $GNGGA.
    Retourne un dict {satellites, lat, lon, alt} ou None si pas de fix.
    Format : $GPGGA,HHMMSS,lat,N,lon,E,fix,sats,hdop,alt,M,...
    $GNGGA = même format, préfixe GNSS multi-constellation (BU-353N5 moderne)
    """
    try:
        if not (sentence.startswith("$GPGGA") or sentence.startswith("$GNGGA")):
            return None
        parts = sentence.split(",")
        fix_quality = int(parts[6]) if parts[6] else 0
        if fix_quality == 0:
            return None
        satellites = int(parts[7]) if parts[7] else 0

        # Latitude
        lat_raw = parts[2]; lat_dir = parts[3]
        lat = float(lat_raw[:2]) + float(lat_raw[2:]) / 60 if lat_raw else None
        if lat and lat_dir == "S": lat = -lat

        # Longitude
        lon_raw = parts[4]; lon_dir = parts[5]
        lon = float(lon_raw[:3]) + float(lon_raw[3:]) / 60 if lon_raw else None
        if lon and lon_dir == "W": lon = -lon

        # Altitude
        alt = float(parts[9]) if len(parts) > 9 and parts[9] else None

        return {"satellites": satellites, "lat": lat, "lon": lon, "alt": alt}
    except Exception:
        return None

# ──────────────────────────────────────────────────────────────────────────────
# SYNCHRONISATION HEURE SYSTÈME
# ──────────────────────────────────────────────────────────────────────────────

def sync_system_time(dt_utc, dry_run=False):
    """
    Synchronise l'heure système Linux avec la datetime UTC fournie.
    Requiert les droits root.
    Retourne True si succès.
    """
    # Format attendu par 'date' : MMDDHHmmYYYY.SS
    date_cmd = dt_utc.strftime("%m%d%H%M%Y.%S")

    # Chemins absolus hardcodés — shutil.which() non fiable sous systemd (PATH vide)
    _date_bin = "/usr/bin/date" if os.path.isfile("/usr/bin/date") else "/bin/date"
    _hwclock_bin = "/sbin/hwclock" if os.path.isfile("/sbin/hwclock") else "/usr/sbin/hwclock"

    # Si non-root, appeler sudo -n (non-interactif, échoue si mot de passe requis)
    prefix = [] if os.geteuid() == 0 else ["/usr/bin/sudo", "-n"]
    cmd = prefix + [_date_bin, "-u", date_cmd]

    logging.info(f"{Colors.CYAN}Commande sync : {' '.join(cmd)}{Colors.RESET}")

    if dry_run:
        logging.info(f"{Colors.CYAN}[DRY-RUN] Commande qui serait exécutée : {' '.join(cmd)}{Colors.RESET}")
        return True

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            logging.info(f"{Colors.GREEN}✅ Heure système synchronisée : {dt_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC{Colors.RESET}")

            # Synchroniser aussi le RTC hardware si présent
            subprocess.run(prefix + [_hwclock_bin, "--systohc"], capture_output=True)
            logging.info(f"{Colors.GREEN}✅ RTC hardware mis à jour.{Colors.RESET}")
            return True
        else:
            logging.error(f"{Colors.RED}Échec de 'date' (code {result.returncode}) : {result.stderr.strip()}{Colors.RESET}")
            return False
    except FileNotFoundError as e:
        logging.error(f"{Colors.RED}Commande introuvable : {e}{Colors.RESET}")
        return False
    except Exception as e:
        logging.error(f"{Colors.RED}Erreur sync heure : {e}{Colors.RESET}")
        return False

def check_root():
    """Vérifie les droits root. Si non-root, on utilisera sudo pour date/hwclock."""
    if os.geteuid() != 0:
        logging.warning(f"{Colors.YELLOW}Non-root : 'date' et 'hwclock' seront appelés via sudo.{Colors.RESET}")

# ──────────────────────────────────────────────────────────────────────────────
# BOUCLE PRINCIPALE
# ──────────────────────────────────────────────────────────────────────────────

FIXES_REQUIRED = 5   # Nombre de fixes consécutifs valides avant synchronisation

def compute_median_time(fix_timestamps):
    """
    Calcule la médiane des timestamps GPS collectés pour éliminer les outliers.
    fix_timestamps : liste de (datetime_utc, reception_time_monotonic)
    Retourne une datetime UTC corrigée du délai de traitement.
    """
    # Calcul de l'écart entre chaque fix GPS et l'horloge locale au moment de réception
    offsets = []
    for dt_gps, t_received in fix_timestamps:
        local_ts = datetime.fromtimestamp(t_received, tz=timezone.utc)
        offset = (dt_gps - local_ts).total_seconds()
        offsets.append(offset)

    offsets_sorted = sorted(offsets)
    median_offset  = offsets_sorted[len(offsets_sorted) // 2]

    # Heure corrigée = heure locale actuelle + offset médian GPS
    now_utc     = datetime.now(tz=timezone.utc)
    corrected   = now_utc.replace(microsecond=0) + __import__("datetime").timedelta(seconds=round(median_offset))

    logging.info(f"{Colors.CYAN}Offsets GPS/système (s) : {[f'{o:+.1f}' for o in offsets_sorted]}{Colors.RESET}")
    logging.info(f"{Colors.CYAN}Offset médian retenu    : {median_offset:+.3f}s{Colors.RESET}")

    # Alerte si la dérive dépasse 2 secondes
    if abs(median_offset) > 2.0:
        logging.warning(
            f"{Colors.ORANGE}⚠️  Dérive horloge système détectée : {median_offset:+.1f}s "
            f"— synchronisation indispensable !{Colors.RESET}"
        )
    elif abs(median_offset) > 0.5:
        logging.info(f"{Colors.YELLOW}Dérive horloge : {median_offset:+.3f}s{Colors.RESET}")
    else:
        logging.info(f"{Colors.GREEN}Dérive horloge : {median_offset:+.3f}s (excellente){Colors.RESET}")

    return corrected

def open_serial(port):
    """Tente d'ouvrir le port série, avec retry infini toutes les 5s."""
    while True:
        try:
            ser = serial.Serial(port, baudrate=BAUD_RATE, timeout=READ_TIMEOUT)
            logging.info(f"{Colors.GREEN}Port série {port} ouvert à {BAUD_RATE} baud.{Colors.RESET}")
            return ser
        except serial.SerialException as e:
            logging.warning(f"{Colors.YELLOW}Port série inaccessible ({e}), retry dans 5s...{Colors.RESET}")
            time.sleep(5)

def wait_for_gps_fix(port, verbose, dry_run):
    """
    Ouvre le port série et tourne indéfiniment jusqu'à obtenir FIXES_REQUIRED
    fixes GPS valides consécutifs, puis synchronise l'heure système.
    - Calcule la médiane des offsets pour une précision à la seconde.
    - Retry infini : ne s'arrête que sur Ctrl+C ou succès.
    Retourne True si synchronisation réussie.
    """
    attempt = 0

    while True:   # Retry infini
        attempt += 1
        if attempt > 1:
            logging.info(f"{Colors.YELLOW}--- Tentative #{attempt} de synchronisation GPS ---{Colors.RESET}")

        ser = open_serial(port)

        satellites     = None
        fix_timestamps = []   # Liste de (datetime_utc, t_monotonic_reception)
        last_progress  = 0

        logging.info(
            f"{Colors.YELLOW}En attente de {FIXES_REQUIRED} fixes GPS consécutifs valides "
            f"(retry infini — Ctrl+C pour annuler)...{Colors.RESET}"
        )

        try:
            while True:
                elapsed = int(time.time() - (fix_timestamps[0][1] if fix_timestamps else time.time()))

                try:
                    raw_line = ser.readline()
                    line = raw_line.decode("ascii", errors="ignore").strip()
                except serial.SerialException as e:
                    logging.warning(f"{Colors.YELLOW}Perte du port série : {e}. Réouverture...{Colors.RESET}")
                    ser.close()
                    fix_timestamps = []
                    ser = open_serial(port)
                    continue

                if not line:
                    continue

                if verbose:
                    logging.debug(f"NMEA : {line}")

                # Satellites + coordonnées depuis GPGGA/GNGGA
                gga = parse_gpgga(line)
                if gga is not None:
                    satellites = gga["satellites"]
                    # Log coordonnées GPS avec lat/lon/alt pour que Flask puisse les parser
                    if gga["lat"] is not None and gga["lon"] is not None:
                        _alt = gga["alt"] if gga["alt"] is not None else 0.0
                        logging.info(
                            f"GPS_COORDS lat={gga['lat']:.6f} lon={gga['lon']:.6f} "
                            f"alt={_alt:.1f} sats={satellites}"
                        )
                    if verbose:
                        logging.info(f"{Colors.BLUE}Satellites visibles : {satellites}{Colors.RESET}")

                # Fix depuis GPRMC
                result = parse_gprmc(line)
                if result:
                    dt_utc, lat, lon, speed = result
                    t_received = time.time()   # timestamp monotonic de réception
                    fix_timestamps.append((dt_utc, t_received))

                    logging.info(
                        f"{Colors.GREEN}Fix {len(fix_timestamps)}/{FIXES_REQUIRED} │ "
                        f"UTC: {dt_utc.strftime('%Y-%m-%d %H:%M:%S')} │ "
                        f"Lat: {lat:.5f}° │ Lon: {lon:.5f}° │ "
                        f"Sats: {satellites if satellites is not None else '?'} │ "
                        f"Speed: {speed:.1f}kt"
                        f"{Colors.RESET}"
                    )

                    if len(fix_timestamps) >= FIXES_REQUIRED:
                        # Calcul médiane et synchronisation
                        logging.info(f"{Colors.CYAN}━━━ {FIXES_REQUIRED} fixes collectés — calcul de la précision ━━━{Colors.RESET}")
                        dt_synced = compute_median_time(fix_timestamps)
                        ser.close()
                        return sync_system_time(dt_synced, dry_run=dry_run)

                else:
                    # Progression toutes les 15s si pas encore de fix
                    now = int(time.time())
                    if now - last_progress >= 15 and not fix_timestamps:
                        last_progress = now
                        logging.info(
                            f"{Colors.YELLOW}⏳ Recherche du signal GPS... "
                            f"(sats visibles : {satellites if satellites is not None else '?'}, "
                            f"fixes : {len(fix_timestamps)}/{FIXES_REQUIRED})"
                            f"{Colors.RESET}"
                        )
                    # Reset UNIQUEMENT si on reçoit un RMC avec status V (perte de fix réelle)
                    # Les trames GGA, GSA, GSV, PAIR ne doivent PAS resetter la séquence
                    if fix_timestamps and is_rmc_void(line):
                        logging.warning(f"{Colors.YELLOW}Fix RMC perdu (status V) — remise à zéro.{Colors.RESET}")
                        fix_timestamps = []

        except KeyboardInterrupt:
            logging.info(f"\n{Colors.RED}Synchronisation GPS annulée par l'utilisateur.{Colors.RESET}")
            ser.close()
            return False
        except Exception as e:
            logging.error(f"{Colors.RED}Erreur inattendue : {e}. Retry...{Colors.RESET}")
            if ser.is_open:
                ser.close()
            time.sleep(3)

# ──────────────────────────────────────────────────────────────────────────────
# POINT D'ENTRÉE
# ──────────────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Synchronisation heure système via GPS USB BU-353N5",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  sudo python3 gps_sync_bu353n5.py\n"
            "  sudo python3 gps_sync_bu353n5.py --port /dev/ttyUSB1\n"
            "  sudo python3 gps_sync_bu353n5.py --verbose\n"
            "  sudo python3 gps_sync_bu353n5.py --dry-run\n"
        )
    )
    parser.add_argument("--port",    type=str, default=None,
                        help="Port série forcé (ex: /dev/ttyUSB0). Détection auto si absent.")
    parser.add_argument("--verbose", action="store_true",
                        help="Affiche toutes les trames NMEA reçues")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simule la synchronisation sans modifier l'heure système")
    return parser.parse_args()

def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    print(f"{Colors.CYAN}")
    print("╔══════════════════════════════════════════════════╗")
    print("║   GPS Time Sync — GlobalSat BU-353N5             ║")
    print("║   Raspberry Pi — Solar Eclipse Trigger           ║")
    print(f"║   Précision : {FIXES_REQUIRED} fixes GPS + médiane              ║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"{Colors.RESET}")

    if not args.dry_run:
        check_root()

    # Déterminer le port (retry infini si GPS pas encore branché)
    port = args.port
    if not port:
        while True:
            port = find_bu353n5_port()
            if port:
                break
            logging.warning(f"{Colors.YELLOW}GPS BU-353N5 non détecté, retry dans 5s... (branchez le GPS){Colors.RESET}")
            time.sleep(5)

    # Lancer la synchronisation (retry infini intégré)
    success = wait_for_gps_fix(
        port     = port,
        verbose  = args.verbose,
        dry_run  = args.dry_run
    )

    if success:
        logging.info(f"{Colors.GREEN}✅ Synchronisation GPS terminée avec succès.{Colors.RESET}")
        sys.exit(0)
    else:
        logging.error(f"{Colors.RED}❌ Synchronisation GPS annulée.{Colors.RESET}")
        sys.exit(1)

if __name__ == "__main__":
    main()
