#!/usr/bin/env python3
"""
zwo_eaf.py
Version : 1.0.03

Module bas niveau de pilotage du focuseur ZWO EAF via le SDK officiel
(libEAFFocuser.so), en ctypes. Equivalent de onstep.py pour la monture :
il enveloppe les fonctions C du SDK et expose une petite API Python propre.
AUCUNE logique "plugin" ici -- juste le pilotage materiel testable seul.

Signatures tirees de EAF_focuser.h (SDK V1.8.1).

Points cles du SDK :
  - EAFMove(ID, step) va a une position ABSOLUE (0..MaxStep) et rend la main
    IMMEDIATEMENT (asynchrone). EAFIsMoving() dit si le moteur tourne encore.
  - Pas de commande "avance en continu" : le mode maintien se simule en
    logiciel (move vers une butee, puis Stop au relachement).
  - Le couple du moteur est important : toujours respecter 0 et MaxStep.
"""

import ctypes
import time

LIB_NAME = "libEAFFocuser.so"
EAF_SUCCESS = 0
EAF_ERROR_NOT_SUPPORTED = 8


# --- structures C (EAF_focuser.h) ------------------------------------------ #
class EAF_INFO(ctypes.Structure):
    _fields_ = [
        ("ID", ctypes.c_int),
        ("Name", ctypes.c_char * 64),
        ("MaxStep", ctypes.c_int),
    ]


class EAF_SN(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_ubyte * 8),
    ]


class EafError(Exception):
    """Erreur SDK EAF (code non nul) ou probleme de pilotage."""
    def __init__(self, code=None, msg=""):
        self.code = code
        super().__init__(msg or f"EAF error code {code}")


def _load_lib():
    # La lib EAF depend de libudev ; on la charge d'abord en mode GLOBAL pour
    # exposer ses symboles (sinon : undefined symbol udev_device_get_devnode).
    for udev_name in ("libudev.so.1", "libudev.so.0", "libudev.so"):
        try:
            ctypes.CDLL(udev_name, mode=ctypes.RTLD_GLOBAL)
            break
        except OSError:
            continue
    try:
        return ctypes.CDLL(LIB_NAME, mode=ctypes.RTLD_GLOBAL)
    except OSError as e:
        raise EafError(msg=f"Impossible de charger {LIB_NAME} : {e}")


class ZwoEaf:
    """Pilotage d'un focuseur ZWO EAF (le premier detecte par defaut)."""

    def __init__(self):
        self.lib = _load_lib()
        self._setup_prototypes()
        self.id = None
        self.name = None
        self.max_step = None

    # ------------------------------------------------------------------ #
    def _setup_prototypes(self):
        L = self.lib
        L.EAFGetNum.restype = ctypes.c_int
        L.EAFGetID.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        L.EAFGetID.restype = ctypes.c_int
        L.EAFOpen.argtypes = [ctypes.c_int]
        L.EAFOpen.restype = ctypes.c_int
        L.EAFClose.argtypes = [ctypes.c_int]
        L.EAFClose.restype = ctypes.c_int
        L.EAFGetProperty.argtypes = [ctypes.c_int, ctypes.POINTER(EAF_INFO)]
        L.EAFGetProperty.restype = ctypes.c_int
        L.EAFMove.argtypes = [ctypes.c_int, ctypes.c_int]
        L.EAFMove.restype = ctypes.c_int
        L.EAFStop.argtypes = [ctypes.c_int]
        L.EAFStop.restype = ctypes.c_int
        L.EAFGetPosition.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        L.EAFGetPosition.restype = ctypes.c_int
        L.EAFResetPostion.argtypes = [ctypes.c_int, ctypes.c_int]
        L.EAFResetPostion.restype = ctypes.c_int
        L.EAFIsMoving.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_bool),
                                  ctypes.POINTER(ctypes.c_bool)]
        L.EAFIsMoving.restype = ctypes.c_int
        L.EAFGetTemp.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
        L.EAFGetTemp.restype = ctypes.c_int
        L.EAFGetMaxStep.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        L.EAFGetMaxStep.restype = ctypes.c_int
        L.EAFSetMaxStep.argtypes = [ctypes.c_int, ctypes.c_int]
        L.EAFSetMaxStep.restype = ctypes.c_int
        L.EAFStepRange.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        L.EAFStepRange.restype = ctypes.c_int
        L.EAFGetSDKVersion.restype = ctypes.c_char_p
        if hasattr(L, "EAFGetSerialNumber"):
            L.EAFGetSerialNumber.argtypes = [
                ctypes.c_int,
                ctypes.POINTER(EAF_SN),
            ]
            L.EAFGetSerialNumber.restype = ctypes.c_int

    def _check(self, code, what):
        if code != EAF_SUCCESS:
            raise EafError(code, f"{what} a echoue (code {code})")

    # ------------------------------------------------------------------ #
    # Connexion
    # ------------------------------------------------------------------ #
    def sdk_version(self):
        v = self.lib.EAFGetSDKVersion()
        return v.decode("ascii", "replace") if v else "?"

    @staticmethod
    def _sdk_id_from_device_id(device_id):
        """Convertit 'zwo_eaf:N' ou N vers l'ID SDK entier."""
        if isinstance(device_id, int) and not isinstance(device_id, bool):
            value = device_id
        else:
            text = str(device_id or "").strip()
            if text.startswith("zwo_eaf:"):
                text = text.split(":", 1)[1]
            if not text:
                raise EafError(msg="device_id EAF vide")
            try:
                value = int(text)
            except ValueError as exc:
                raise EafError(
                    msg=f"device_id EAF invalide : {device_id}"
                ) from exc

        if not 0 <= value <= 127:
            raise EafError(msg=f"device_id EAF hors bornes : {value}")
        return value

    def _serial_number(self, sdk_id):
        """Lit le serial matériel s'il est supporté par le firmware."""
        getter = getattr(self.lib, "EAFGetSerialNumber", None)
        if getter is None:
            return None

        serial = EAF_SN()
        code = getter(int(sdk_id), ctypes.byref(serial))

        if code == EAF_ERROR_NOT_SUPPORTED:
            return None

        self._check(code, "EAFGetSerialNumber")

        raw = bytes(serial.id)
        if not any(raw):
            return None

        return raw.hex().upper()

    def enumerate_devices(self):
        """Enumère tous les EAF sans envoyer aucune commande de mouvement."""
        count = self.lib.EAFGetNum()
        if count <= 0:
            return []

        devices = []

        for index in range(count):
            cid = ctypes.c_int(0)

            try:
                self._check(
                    self.lib.EAFGetID(index, ctypes.byref(cid)),
                    "EAFGetID",
                )
                sdk_id = cid.value

                self._check(self.lib.EAFOpen(sdk_id), "EAFOpen")
                try:
                    info = EAF_INFO()
                    self._check(
                        self.lib.EAFGetProperty(
                            sdk_id, ctypes.byref(info)
                        ),
                        "EAFGetProperty",
                    )

                    name = (
                        info.Name.decode("ascii", "replace")
                        .rstrip("\x00")
                        .strip()
                    )

                    devices.append({
                        "category": "focuser",
                        "backend": "zwo_eaf",
                        "manufacturer": "ZWO",
                        "model": name or "EAF",
                        "serial": self._serial_number(sdk_id),
                        "device_id": f"zwo_eaf:{sdk_id}",
                        "sdk_id": sdk_id,
                        "max_step": info.MaxStep,
                    })
                finally:
                    self.lib.EAFClose(sdk_id)

            except EafError:
                continue

        return devices

    def connect(self, index=0, device_id=None):
        """Ouvre un EAF par device_id explicite ou, en legacy, par index."""
        n = self.lib.EAFGetNum()
        if n <= 0:
            raise EafError(
                msg="Aucun EAF detecte (branche ? alimente 12V ?)"
            )

        if device_id is None:
            if index >= n:
                raise EafError(msg=f"Index {index} hors bornes (n={n})")

            cid = ctypes.c_int(0)
            self._check(
                self.lib.EAFGetID(index, ctypes.byref(cid)),
                "EAFGetID",
            )
            self.id = cid.value
        else:
            self.id = self._sdk_id_from_device_id(device_id)

        self._check(self.lib.EAFOpen(self.id), "EAFOpen")

        info = EAF_INFO()
        self._check(
            self.lib.EAFGetProperty(self.id, ctypes.byref(info)),
            "EAFGetProperty",
        )

        self.name = (
            info.Name.decode("ascii", "replace")
            .rstrip("\x00")
            .strip()
        )
        self.max_step = info.MaxStep

        return {
            "id": self.id,
            "device_id": f"zwo_eaf:{self.id}",
            "serial": self._serial_number(self.id),
            "name": self.name,
            "max_step": self.max_step,
        }

    def disconnect(self):
        if self.id is not None:
            try:
                self.lib.EAFStop(self.id)
            except Exception:
                pass
            self.lib.EAFClose(self.id)
            self.id = None

    @property
    def connected(self):
        return self.id is not None

    def _require(self):
        if self.id is None:
            raise EafError(msg="EAF non connecte")

    # ------------------------------------------------------------------ #
    # Lecture d'etat
    # ------------------------------------------------------------------ #
    def get_position(self):
        self._require()
        pos = ctypes.c_int(0)
        self._check(self.lib.EAFGetPosition(self.id, ctypes.byref(pos)),
                    "EAFGetPosition")
        return pos.value

    def is_moving(self):
        self._require()
        moving = ctypes.c_bool(False)
        hand = ctypes.c_bool(False)
        self._check(self.lib.EAFIsMoving(self.id, ctypes.byref(moving),
                                         ctypes.byref(hand)), "EAFIsMoving")
        return bool(moving.value), bool(hand.value)

    def get_temperature(self):
        self._require()
        t = ctypes.c_float(0.0)
        code = self.lib.EAFGetTemp(self.id, ctypes.byref(t))
        if code != EAF_SUCCESS:
            return None            # temperature indisponible (ex. -273)
        return round(t.value, 2)

    def get_max_step(self):
        self._require()
        v = ctypes.c_int(0)
        self._check(self.lib.EAFGetMaxStep(self.id, ctypes.byref(v)),
                    "EAFGetMaxStep")
        return v.value

    def set_max_step(self, value):
        """Definit la limite haute LOGICIELLE (protection butee mecanique).
        Relit et memorise la valeur pour que _clamp l'utilise."""
        self._require()
        self._check(self.lib.EAFSetMaxStep(self.id, int(value)),
                    "EAFSetMaxStep")
        self.max_step = self.get_max_step()
        return self.max_step

    def status(self):
        pos = self.get_position()
        moving, hand = self.is_moving()
        return {
            "id": self.id,
            "name": self.name,
            "position": pos,
            "max_step": self.max_step,
            "moving": moving,
            "hand_control": hand,
            "temperature": self.get_temperature(),
        }

    # ------------------------------------------------------------------ #
    # Mouvement
    # ------------------------------------------------------------------ #
    def _clamp(self, target):
        """Borne la cible dans [0, max_step] pour proteger la mecanique."""
        lo, hi = 0, (self.max_step if self.max_step else target)
        return max(lo, min(hi, int(target)))

    # Vitesse mesuree de l'EAF : ~365 pas/s. On prend une marge (250 pas/s)
    # pour calculer un timeout d'attente proportionnel a la distance, afin de
    # ne JAMAIS couper un long deplacement (0->60000 ~ 165 s reels).
    STEPS_PER_SEC = 250.0

    def _travel_timeout(self, distance):
        return max(10.0, abs(distance) / self.STEPS_PER_SEC + 10.0)

    def move_to(self, position, wait=False, timeout=None):
        """Va a une position ABSOLUE (bornee 0..max_step). Asynchrone sauf si
        wait=True. Le timeout d'attente est calcule selon la distance si non
        fourni, pour ne pas couper un long deplacement."""
        self._require()
        current = self.get_position()
        target = self._clamp(position)
        self._check(self.lib.EAFMove(self.id, target), "EAFMove")
        if wait:
            t = timeout if timeout is not None \
                else self._travel_timeout(target - current)
            self.wait_stopped(t)
        return target

    def move_relative(self, delta, wait=False, timeout=None):
        """Avance de `delta` pas (+/-) par rapport a la position courante."""
        return self.move_to(self.get_position() + int(delta),
                            wait=wait, timeout=timeout)

    def stop(self):
        self._require()
        self._check(self.lib.EAFStop(self.id), "EAFStop")

    def wait_stopped(self, timeout=180.0, poll=0.05):
        self._require()
        t0 = time.monotonic()
        while (time.monotonic() - t0) < timeout:
            moving, _ = self.is_moving()
            if not moving:
                return True
            time.sleep(poll)
        return False

    def set_current_position(self, value):
        """Definit la valeur de la position courante (pose le zero/reference)."""
        self._require()
        self._check(self.lib.EAFResetPostion(self.id, int(value)),
                    "EAFResetPostion")

    # ------------------------------------------------------------------ #
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *a):
        self.disconnect()


# --------------------------------------------------------------------------- #
# Auto-test bas niveau (python3 zwo_eaf.py)  -- NE FAIT PAS bouger le moteur
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    eaf = ZwoEaf()
    print("SDK version :", eaf.sdk_version())
    info = eaf.connect()
    print("Connecte :", info)
    print("Statut   :", eaf.status())
    eaf.disconnect()
    print("Deconnecte.")
