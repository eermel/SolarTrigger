"""
onstep.py
Version : 1.1.00

Pilote bas niveau OnStep / Tessek (LX200 serie). Perimetre : tracking,
mouvements manuels, estop, recentrage HOME (avec setup date/heure/position
+ unpark, comme un ASIAIR). Pas de GoTo.
"""

from __future__ import annotations

import threading
import time
from enum import Enum

import serial


# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
DEFAULT_BAUDRATE = 9600
DEFAULT_TIMEOUT = 1.0


# ============================================================
# EXCEPTIONS
# ============================================================

class OnStepError(Exception):
    """Erreur de communication ou de commande OnStep."""
    pass


# ============================================================
# ENUMERATIONS
# ============================================================

class TrackingRate(Enum):
    SIDEREAL = "sidereal"
    SOLAR = "solar"
    LUNAR = "lunar"


class Direction(Enum):
    DEC_LEFT = "DEC gauche"
    DEC_RIGHT = "DEC droite"
    AD_RIGHT = "AD droite"
    AD_LEFT = "AD gauche"


# ============================================================
# MONTURE ONSTEP
# ============================================================

class OnStep:

    # --------------------------------------------------------
    # COMMANDES DE DIRECTION
    #
    # Correspondance physique déterminée sur ta Teseek :
    #
    # Mn = DEC gauche
    # Ms = DEC droite
    # Me = AD droite
    # Mw = AD gauche
    # --------------------------------------------------------

    DIRECTION_COMMANDS = {
        Direction.DEC_LEFT: b":Mn#",
        Direction.DEC_RIGHT: b":Ms#",
        Direction.AD_RIGHT: b":Me#",
        Direction.AD_LEFT: b":Mw#",
    }

    # --------------------------------------------------------
    # VITESSES DE MOUVEMENT
    #
    # R0 = 0.25x
    # R1 = 0.5x
    # R2 = 1x
    # R3 = 2x
    # R4 = 4x
    # R5 = 8x
    # R6 = 16x
    # R7 = 24x
    # R8 = 40x
    # R9 = 60x
    # --------------------------------------------------------

    MOVE_RATES = {
        0.25: b":R0#",
        0.5: b":R1#",
        1.0: b":R2#",
        2.0: b":R3#",
        4.0: b":R4#",
        8.0: b":R5#",
        16.0: b":R6#",
        24.0: b":R7#",
        40.0: b":R8#",
        60.0: b":R9#",
    }

    # ========================================================
    # CONSTRUCTEUR
    # ========================================================

    def __init__(
        self,
        port=DEFAULT_PORT,
        baudrate=DEFAULT_BAUDRATE,
        timeout=DEFAULT_TIMEOUT,
    ):

        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

        self.serial = None
        self._serial_lock = threading.RLock()

        # Etat local
        self._move_rate = 4.0
        self._tracking_rate = None

    # ========================================================
    # CONNEXION
    # ========================================================

    def connect(self):

        with self._serial_lock:
            if self.connected:
                return

            try:

                self.serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=self.timeout,
                    write_timeout=self.timeout,
                )

                # Nettoyage des buffers
                self.serial.reset_input_buffer()
                self.serial.reset_output_buffer()

            except serial.SerialException as exc:

                self.serial = None

                raise OnStepError(
                    f"Impossible de se connecter à "
                    f"{self.port}: {exc}"
                ) from exc

    def disconnect(self):

        with self._serial_lock:
            if self.serial is not None:

                try:
                    self.serial.close()

                finally:
                    self.serial = None

    @property
    def connected(self):

        return (
            self.serial is not None
            and self.serial.is_open
        )

    def reconnect(self):

        with self._serial_lock:
            self.disconnect()

            time.sleep(0.2)

            self.connect()

    # ========================================================
    # COMMUNICATION BAS NIVEAU
    # ========================================================

    def _serial(self):

        if not self.connected:

            raise OnStepError(
                "Monture OnStep non connectée."
            )

        return self.serial

    def _send(self, command):

        with self._serial_lock:
            ser = self._serial()

            try:

                ser.write(command)
                ser.flush()

            except serial.SerialException as exc:

                raise OnStepError(
                    f"Erreur d'envoi {command!r}: {exc}"
                ) from exc

    def _query(
        self,
        command,
        timeout=None,
    ):

        with self._serial_lock:
            ser = self._serial()

            old_timeout = ser.timeout

            try:

                if timeout is not None:
                    ser.timeout = timeout

                ser.reset_input_buffer()

                ser.write(command)
                ser.flush()

                response = ser.read_until(b"#")

                return response.rstrip(b"#")

            except serial.SerialException as exc:

                raise OnStepError(
                    f"Erreur de communication : {exc}"
                ) from exc

            finally:

                ser.timeout = old_timeout

    def _query_text(
        self,
        command,
        timeout=None,
    ):

        response = self._query(
            command,
            timeout=timeout,
        )

        if not response:

            raise OnStepError(
                f"Aucune réponse à {command!r}"
            )

        try:

            return response.decode("ascii")

        except UnicodeDecodeError as exc:

            raise OnStepError(
                f"Réponse ASCII invalide : "
                f"{response!r}"
            ) from exc

    def _query_bool(
        self,
        command,
        timeout=2.0,
    ):
        """Envoie une commande dont OnStep repond par un seul caractere
        '1' (succes) ou '0' (echec), SANS terminateur '#'.
        Utilise pour les commandes de setup (:St :Sg :SG :SL :SC) et
        de park/unpark (:hR :hP ...). Retourne True si '1', False sinon."""

        with self._serial_lock:
            ser = self._serial()
            old_timeout = ser.timeout

            try:

                ser.timeout = timeout
                ser.reset_input_buffer()
                ser.write(command)
                ser.flush()

                resp = ser.read(1)
                return resp == b"1"

            except serial.SerialException as exc:

                raise OnStepError(
                    f"Erreur de communication : {exc}"
                ) from exc

            finally:

                ser.timeout = old_timeout

    # ========================================================
    # INFORMATIONS
    # ========================================================

    def get_product(self):

        return self._query_text(
            b":GVP#"
        )

    def get_firmware(self):

        return self._query_text(
            b":GVN#"
        )

    def get_ra(self):

        return self._query_text(
            b":GR#"
        )

    def get_dec(self):

        return self._query_text(
            b":GD#"
        )

    def get_sidereal_time(self):

        return self._query_text(
            b":GS#"
        )

    # ========================================================
    # STATUS ONSTEP
    # ========================================================

    def get_status_raw(self):

        return self._query_text(
            b":GU#"
        )

    def is_tracking(self):

        status = self.get_status_raw()

        # OnStep :
        #
        # n = NOT tracking
        # absence de n = tracking
        #
        # Important :
        # le T du statut n'est PAS le tracking.

        return "n" not in status

    def is_at_home(self):

        status = self.get_status_raw()

        # Le flag "At Home" est le caractere H dans le statut OnStep :GU#.
        # On le cherche dans la zone des flags d'etat (positions 3-6),
        # pas n'importe ou, pour eviter les faux positifs.
        return "H" in status[3:7]

    def is_goto_active(self):

        status = self.get_status_raw()

        # N = aucun goto
        return "N" not in status

    def get_park_status(self):

        status = self.get_status_raw()

        # Flag park positionnel : caractere [2] du statut :GU#.
        #   P = parked, I = parking en cours, F = park failed, autre = not parked
        flag = status[2] if len(status) > 2 else ""

        if flag == "P":
            return "parked"

        if flag == "I":
            return "parking"

        if flag == "F":
            return "failed"

        return "not_parked"

    # ========================================================
    # ETAT DE LA MONTURE
    # ========================================================

    def status(self):

        raw = self.get_status_raw()

        return {
            "connected": self.connected,
            "product": self.get_product(),
            "firmware": self.get_firmware(),

            "ra": self.get_ra(),
            "dec": self.get_dec(),
            "sidereal_time": self.get_sidereal_time(),

            "raw": raw,

            "tracking": "n" not in raw,

            "at_home": "H" in raw,

            "goto_active": "N" not in raw,

            "park_status": self.get_park_status(),

            "move_rate": self._move_rate,

            "tracking_rate": (
                self._tracking_rate.value
                if self._tracking_rate is not None
                else None
            ),
        }

    # ========================================================
    # VITESSE DE DEPLACEMENT
    # ========================================================

    @property
    def move_rate(self):

        return self._move_rate

    def set_move_rate(self, rate):

        rate = float(rate)

        if rate not in self.MOVE_RATES:

            available = ", ".join(
                f"{x:g}x"
                for x in self.MOVE_RATES.keys()
            )

            raise ValueError(
                f"Vitesse invalide : {rate}x\n"
                f"Disponibles : {available}"
            )

        self._send(
            self.MOVE_RATES[rate]
        )

        self._move_rate = rate

    # ========================================================
    # MOUVEMENT
    # ========================================================

    def move(self, direction):

        if not isinstance(
            direction,
            Direction,
        ):

            raise ValueError(
                "Direction invalide."
            )

        self._send(
            self.DIRECTION_COMMANDS[
                direction
            ]
        )

    # --------------------------------------------------------
    # Raccourcis
    # --------------------------------------------------------

    def dec_left(self):

        self.move(
            Direction.DEC_LEFT
        )

    def dec_right(self):

        self.move(
            Direction.DEC_RIGHT
        )

    def ad_right(self):

        self.move(
            Direction.AD_RIGHT
        )

    def ad_left(self):

        self.move(
            Direction.AD_LEFT
        )

    # ========================================================
    # STOP
    # ========================================================

    def stop(self):

        # :Q# = arrêt des mouvements
        self._send(b":Q#")

    # ========================================================
    # TRACKING
    # ========================================================

    def select_tracking_rate(
        self,
        rate,
    ):

        if not isinstance(
            rate,
            TrackingRate,
        ):

            rate = TrackingRate(rate)

        # Commandes OnStep/LX200
        #
        # TQ = sidéral
        # TS = solaire
        # TL = lunaire

        commands = {
            TrackingRate.SIDEREAL: b":TQ#",
            TrackingRate.SOLAR: b":TS#",
            TrackingRate.LUNAR: b":TL#",
        }

        self._send(
            commands[rate]
        )

        self._tracking_rate = rate

    def start_tracking(
        self,
        rate=TrackingRate.SIDEREAL,
    ):

        if not isinstance(
            rate,
            TrackingRate,
        ):

            rate = TrackingRate(rate)

        # Sélection du taux
        self.select_tracking_rate(
            rate
        )

        # :Te# = tracking enable
        #
        # Retour attendu :
        # 1 = accepté

        response = self._query(
            b":Te#",
            timeout=0.5,
        )

        if response != b"1":

            raise OnStepError(
                f"Impossible de démarrer "
                f"le tracking : {response!r}"
            )

        # Petite attente pour laisser
        # OnStep mettre son état à jour.
        time.sleep(0.05)

    def stop_tracking(self):

        # :Td# = tracking disable
        response = self._query(
            b":Td#",
            timeout=0.5,
        )

        if response not in (
            b"0",
            b"1",
        ):

            raise OnStepError(
                f"Réponse inattendue à "
                f":Td# : {response!r}"
            )

        self._tracking_rate = None

        # Arrêt de sécurité
        self.stop()

    @property
    def tracking_rate(self):

        return self._tracking_rate

    # ========================================================
    # SETUP DATE / HEURE / POSITION  (prealable a l'unpark)
    # ========================================================
    # OnStep refuse l'unpark (:hR# -> 0) tant qu'il n'a pas recu une
    # date, une heure et une position valides. C'est ce que fait un
    # ASIAIR au demarrage. Ces methodes envoient ce setup.
    #
    # Conventions OnStep / LX200 (pieges a connaitre) :
    #  - latitude  :St sDD*MM#     (Sud negatif, comme la geo)
    #  - longitude :Sg sDDD*MM#    OUEST POSITIF -> on inverse le signe
    #                              par rapport a la convention geo (Est<0)
    #  - offset    :SG sHH#        convention inversee : on envoie -offset
    #  - heure loc :SL HH:MM:SS#   heure LOCALE (= UTC + offset)
    #  - date      :SC MM/DD/YY#

    @staticmethod
    def _deg_to_dm(value):
        """Degres decimaux -> (signe, degres, minutes entieres)."""
        sign = "+" if value >= 0 else "-"
        v = abs(value)
        d = int(v)
        m = int(round((v - d) * 60))
        if m == 60:
            d += 1
            m = 0
        return sign, d, m

    def set_location(self, lat_deg, lon_deg):
        """Envoie latitude/longitude a OnStep (degres decimaux signes,
        convention geo : Nord+, Est+). Retourne True si accepte."""
        la_s, la_d, la_m = self._deg_to_dm(lat_deg)
        # longitude OnStep : Ouest positif -> inverser le signe geo
        lo_s, lo_d, lo_m = self._deg_to_dm(-lon_deg)
        ok_lat = self._query_bool(
            f":St{la_s}{la_d:02d}*{la_m:02d}#".encode()
        )
        ok_lon = self._query_bool(
            f":Sg{lo_s}{lo_d:03d}*{lo_m:02d}#".encode()
        )
        return ok_lat and ok_lon

    def set_datetime(self, local_dt, utc_offset):
        """Envoie l'heure locale, l'offset UTC et la date a OnStep.
        local_dt : datetime LOCAL (= UTC + utc_offset).
        utc_offset : decalage local vs UTC (France ete=2, hiver=1).
        Retourne True si tout accepte."""
        # OnStep :SG = offset avec convention inversee
        sg = -utc_offset
        ok_off = self._query_bool(
            f":SG{'+' if sg >= 0 else '-'}{abs(sg):02d}#".encode()
        )
        ok_time = self._query_bool(
            f":SL{local_dt.strftime('%H:%M:%S')}#".encode()
        )
        ok_date = self._query_bool(
            f":SC{local_dt.strftime('%m/%d/%y')}#".encode()
        )
        return ok_off and ok_time and ok_date

    def set_datetime_location(
        self,
        dt_utc,
        lat_deg,
        lon_deg,
        utc_offset,
    ):
        """Setup complet : position + date/heure (a partir d'un datetime UTC).
        Reproduit l'initialisation que fait un ASIAIR. Retourne True si OK."""
        from datetime import timedelta
        local_dt = dt_utc + timedelta(hours=utc_offset)
        ok_loc = self.set_location(lat_deg, lon_deg)
        ok_dt = self.set_datetime(local_dt, utc_offset)
        return ok_loc and ok_dt

    # ========================================================
    # PARK / UNPARK
    # ========================================================

    def unpark(self):
        """Sort la monture de l'etat park (:hR#). NECESSITE que le setup
        date/heure/position ait ete fait avant, sinon OnStep refuse (0).
        Retourne True si l'unpark est accepte."""
        return self._query_bool(b":hR#")

    def park(self):
        """Met la monture en park (:hP#). Retourne True si accepte."""
        return self._query_bool(b":hP#")

    def is_parked(self):
        """True si la monture est parkee (flag positionnel [2] == 'P')."""
        raw = self.get_status_raw()
        return len(raw) > 2 and raw[2] == "P"

    # ========================================================
    # HOME
    # ========================================================

    def find_home(self):

        # :hC# = retour a la position Home connue du controleur
        #
        # OnStep ne renvoie aucune réponse.
        self._send(b":hC#")

    def wait_for_home(
        self,
        timeout=120.0,
        poll_interval=0.5,
        is_cancelled=None,
    ):

        deadline = (
            time.monotonic()
            + timeout
        )

        while time.monotonic() < deadline:

            if callable(is_cancelled) and is_cancelled():

                return False

            if self.is_at_home():

                if callable(is_cancelled) and is_cancelled():

                    return False

                return True

            time.sleep(
                poll_interval
            )

            if callable(is_cancelled) and is_cancelled():

                return False

        raise OnStepError(
            "Timeout : HOME non atteint."
        )

    def go_home(
        self,
        timeout=120.0,
        dt_utc=None,
        lat_deg=None,
        lon_deg=None,
        utc_offset=None,
        is_cancelled=None,
    ):
        """Retourne a la position Home connue du controleur OnStep,
        SANS verrouiller la monture (ce n'est pas un park).

        Sans capteurs Home physiques, apres une perte d'alimentation arbitraire,
        l'orientation absolue des axes peut ne pas etre recuperable
        automatiquement. Cette fonction n'ajoute ni Set Home ni recherche
        d'index.

        Sequence complete validee sur OnStep 4.24s :
          1. (optionnel) setup date/heure/position -- requis si la monture
             n'a jamais recu ces infos, sinon l'unpark echoue.
          2. unpark si la monture est parkee (:hR#) -- une monture parkee
             refuse tout mouvement.
          3. :hC# (find home) puis attente du flag H.

        Si dt_utc/lat/lon/utc_offset sont fournis, le setup est envoye.
        Sinon on suppose que la monture est deja initialisee.
        """

        # 1. Setup date/heure/position si fourni (permet l'unpark)
        if (
            dt_utc is not None
            and lat_deg is not None
            and lon_deg is not None
            and utc_offset is not None
        ):
            self.set_datetime_location(
                dt_utc, lat_deg, lon_deg, utc_offset
            )

        # 2. Dé-parker si nécessaire (une monture parkée refuse de bouger)
        if self.is_parked():
            if not self.unpark():
                raise OnStepError(
                    "Unpark refuse (:hR# -> 0). Date/heure/position "
                    "manquantes ou invalides : fournir dt_utc/lat/lon/"
                    "utc_offset a go_home()."
                )
            # petit délai pour que l'état se stabilise
            time.sleep(1.0)

        # 3. Arrêt de sécurité + tracking off avant le retour home
        self.stop()
        if self.is_tracking():
            self.stop_tracking()
        self.stop()

        # 4. Find home (:hC#) — mouvement autonome de la monture
        self.find_home()

        # 5. Attente de l'arrivée (flag H)
        reached_home = self.wait_for_home(
            timeout=timeout,
            is_cancelled=is_cancelled,
        )

        if not reached_home:
            return False

        if callable(is_cancelled) and is_cancelled():
            return False

        # 6. Arrêt final de sécurité
        self.stop()

        return True

    # ========================================================
    # UTILITAIRE : TEST CONNEXION
    # ========================================================

    def ping(self):

        try:

            product = self.get_product()

            firmware = self.get_firmware()

            return {
                "ok": True,
                "product": product,
                "firmware": firmware,
            }

        except Exception as exc:

            return {
                "ok": False,
                "error": str(exc),
            }

    # ========================================================
    # CONTEXTE PYTHON
    # ========================================================

    def __enter__(self):

        self.connect()

        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):

        try:

            if self.connected:
                self.stop()

        except Exception:
            pass

        self.disconnect()
