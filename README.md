# Solar Eclipse Trigger — v7.0

**Objectif : stabilité temporelle pour l’éclipse totale du 2 août 2027 en Égypte.**

La v7.0 durcit l’autorité de temps : UTC absolu côté backend, scheduler `time.monotonic()`, deadlines caméra monotonic et IHM asservie à la Pi. Voir `TIME_AUDIT_V7.md`.


## Version 6.4

Le déclenchement réel et le mode secours utilisent maintenant l'architecture plugins caméra. Le Sony A7 V utilise le plugin Sony (brackets internes, `shutterspeed`), tandis que le D850 reste géré par le plugin Nikon (photo par photo, fallback `shutterspeed2`/`shutterspeed`). Les timings de phase ne sont plus lus depuis les profils `camera_*.json`.

# 🌑 SolarEclipse Trigger — Raspberry Pi 3B

Système autonome de déclenchement automatique d'appareil photo  
pour éclipses solaires totales, sans connexion internet.

**Matériel :**
- Raspberry Pi 3B
- Appareil photo : Nikon D850 (via USB / gphoto2)
- GPS : GlobalSat BU-353N5 (USB, VID:067b PID:2303)

---

## 📁 Structure du package

```
solareclipse_package/
├── install/
│   ├── install_solareclipse.sh     ← Installation complète (sudo)
│   └── install_python_deps.sh      ← Dépendances Python seules (sans sudo)
├── scripts/
│   ├── gps_sync_bu353n5_v2.py      ← Synchronisation heure via GPS
│   ├── eclipse_calculator_jubier.py ← Calcul C1/C2/TMAX/C3/C4 (JS Jubier)
│   └── Total_Solar_Eclipse_Trigger_script_v3_8_2_pi_only.py ← Trigger photo
├── jubier_files/                   ← JS de Xavier Jubier (algorithme)
│   ├── index.html
│   ├── SolarEclipseTimerSVG_VML.js
│   ├── SolarEclipseTimerDefaultSettings.js
│   ├── NewPopWindow.js
│   └── communprivate.css
└── configs/
    └── espagne.json                ← Exemple config Madrid 2026
```

---

## 🚀 Installation

### Installation complète (première fois)

```bash
cd solareclipse_package/install
chmod +x install_solareclipse.sh
sudo ./install_solareclipse.sh
```

Ce script installe :
- Mise à jour système
- Hotspot WiFi (nmcli)
- Chromium + ChromeDriver
- gpsd + chrony (GPS → NTP)
- Python + venv + dépendances
- Copie des scripts dans `~/python_solareclipsetrigger/`
- Scripts de lancement dans `~/bin/`

### Mise à jour des scripts uniquement

```bash
cd solareclipse_package/install
chmod +x install_python_deps.sh
./install_python_deps.sh
```


---

## 📋 Workflow le jour J

### 1. Synchronisation de l'heure via GPS

```bash
sudo python3 ~/python_solareclipsetrigger/gps_sync_bu353n5_v2.py
# Options :
#   --port /dev/ttyUSB0    (port GPS, détection auto par défaut)
#   --verbose              (affiche toutes les trames NMEA)
#   --dry-run              (transpose la chronologie à maintenant, utilise le même moteur et le matériel caméra ; ne modifie pas l'horloge système)
```

Le script attend un signal GPS, accumule 5 fixes consécutifs,
calcule l'offset médian et synchronise l'horloge système.
**Débrancher le GPS après la sync** pour le passer sur un autre Pi.

### 2. Calcul des circonstances de l'éclipse

```bash
cd ~/python_solareclipsetrigger
source venv/bin/activate

python3 eclipse_calculator_jubier.py \
    --lat 25.6872 --lon 32.6396 --alt 80 \
    --tz 2 --eclipse 2027-08-02

# Génère : todayeclipse.json
```

**Arguments :**
| Argument | Description | Exemple |
|----------|-------------|---------|
| `--lat` | Latitude décimale (+ Nord) | `25.6872` |
| `--lon` | Longitude décimale (+ Est) | `32.6396` |
| `--alt` | Altitude en mètres | `80` |
| `--tz`  | Offset UTC total (DST inclus) | `2` pour UTC+2 |
| `--eclipse` | Clé éclipse | `2026-08-12` ou `2027-08-02` |
| `--output` | Fichier de sortie | `todayeclipse.json` (défaut) |
| `--list` | Lister les éclipses disponibles | — |

**Éclipses disponibles :**
- `2026-08-12` — Totale, Espagne/Méditerranée
- `2027-08-02` — Totale, Égypte (Louxor, ~6m27s)
- `2028-07-22`, `2030-11-25`, `2034-03-20`, `2035-09-02`

### 3. Lancement du trigger

```bash
sudo python3 ~/python_solareclipsetrigger/Total_Solar_Eclipse_Trigger_script_v3_8_2_pi_only.py \
    --file todayeclipse.json
```

**Options trigger :**
```bash
# Avec paramètres en ligne de commande (override le JSON)
sudo python3 ...trigger... \
    --file todayeclipse.json \
    --interact            # mode interactif pour modifier les valeurs

# Commandes interactives pendant l'exécution :
#   Ctrl+C : arrêt propre
```

---

## ⚙️ Fichier de configuration JSON

Le fichier `todayeclipse.json` (généré automatiquement par `eclipse_calculator_jubier.py`) :

```json
{
    "_comment": "Calculé par eclipse_calculator_jubier.py",
    "_type": "Totale",
    "_duration": "6m 27s",
    "C1":    "08:24:10",   ← 1er contact (début partielle) — UTC
    "C2":    "09:21:15",   ← 2e contact (début totalité)   — UTC
    "C3":    "09:27:42",   ← 3e contact (fin totalité)     — UTC
    "C4":    "10:28:05",   ← 4e contact (fin partielle)    — UTC
    "TMAX":  "09:24:28",   ← Maximum éclipse               — UTC
    "TSTART":"07:24:10",   ← Début script (C1 - 1h)        — UTC
    "TEND":  "11:28:05",   ← Fin script (C4 + 1h)          — UTC

    "interval_partial":      180,   ← 1 photo toutes les 3min (partielle)
    "interval_diamond_ring":   4,   ← 1 photo toutes les 4s  (diamond ring)
    "duree_diamond_ring":     40,   ← Durée diamond ring : 40s avant/après totalité
    "shutterspeed_partial":  "1/500",
    "shutterspeed_diamondring": "1/500"
}
```

⚠️ **Tous les temps sont en UTC.** Le script trigger travaille en UTC.

---

## 🔧 Séquence de prise de vue

```
TSTART ─────────────────────────────────────────────────── TEND
   │                                                          │
   ├── Phase 1a (partielle) ─────────────────── C2-40s       │
   │   interval=180s, vitesse=1/500                          │
   ├── Phase 1b (diamond ring avant) ─────────── C2          │
   │   interval=4s,   vitesse=1/500,  durée=40s             │
   ├── Phase 2 (TOTALITÉ) ────────────────── C3              │
   │   Bracketing 15 vitesses : 1/1000 → 1s                 │
   ├── Phase 3a (diamond ring après) ─────────── C3+40s      │
   │   interval=4s,   vitesse=1/500,  durée=40s             │
   └── Phase 3b (partielle retour) ──────────────────── TEND │
       interval=180s, vitesse=1/500
```

---

## 🛠️ Dépannage

### GPS non détecté
```bash
# Vérifier le port
ls /dev/ttyUSB*
# Tester manuellement
gpspipe -w -n 5
# Logs
cat /var/log/gps_sync.log
```

### Chromium headless ne se lance pas
```bash
# Vérifier l'installation
chromium --version
chromedriver --version
# Tester Playwright
python3 -c "from playwright.sync_api import sync_playwright; print('OK')"
```

### Appareil photo non reconnu
```bash
# Tester gphoto2
gphoto2 --auto-detect
gphoto2 --summary
# Débloquer GVFS
pkill -f gvfs-gphoto2
```

### Vérifier la synchronisation NTP/GPS
```bash
chronyc tracking
chronyc sources -v
timedatectl
```

---

## 📅 Éclipses ciblées

| Éclipse | Date | Lieu optimal | Durée max |
|---------|------|--------------|-----------|
| **2026-08-12** | 12 Août 2026 | Valence, Espagne | ~1m30s |
| **2027-08-02** | 2 Août 2027 | Louxor, Égypte | **6m27s** |

---

## 📝 Versions

- `Total_Solar_Eclipse_Trigger_script_v3_8_2_pi_only.py` — version courante
  - v3.8.0 : correction aliases couleurs (Colors.BLEU/JAUNE)
  - v3.8.1 : détection caméra USB dynamique (gp.Camera.autodetect())
  - v3.8.2 : support fichier JSON de configuration (--file)

- `gps_sync_bu353n5_v2.py` — synchronisation GPS
  - Détection automatique port USB (VID:067b PID:2303)
  - 5 fixes consécutifs + médiane des offsets
  - `sudo python3 gps_sync_bu353n5_v2.py [--port /dev/ttyUSBx] [--verbose] [--dry-run]`

- `eclipse_calculator_jubier.py` — calculateur éclipse
  - Utilise le JS original de Xavier Jubier via Flask + Playwright
  - Génère `todayeclipse.json`

---

## Migration vers le moteur Python local

La migration remplace le calcul JS piloté par navigateur par un moteur Python
alimenté par des datasets versionnés. Elle ne modifie ni l'IHM, ni ses routes et
formats d'échange, ni la séquence de déclenchement. Le calculateur historique
`scripts/eclipse_calculator_jubier.py` reste l'oracle de comparaison pendant la
validation ; Playwright n'est requis que pour ce test différentiel (voir son guide
d'installation officiel), pas pour le moteur Python en exploitation.

### Générer les datasets

L'outil `scripts/eclipse_dataset_builder.py` lit exclusivement les sources locales
`jubier_files/index.html` (`select#eclipse_index`) et
`jubier_files/SolarEclipseTimerSVG_VML.js` (`elements`). Il écrit dans
`data/eclipses/` : un fichier `<YYYY-MM-DD>.json` par éclipse et, pour
`build-all`, le registre `registry.json`.

Depuis la racine du dépôt :

```bash
python3 scripts/eclipse_dataset_builder.py list
python3 scripts/eclipse_dataset_builder.py build-all
python3 scripts/eclipse_dataset_builder.py build-one 2027-08-02
```

- `list` inventorie les options valides sans écrire de fichier.
- `build-all` régénère tous les datasets valides et remplace `registry.json`.
- `build-one` remplace uniquement le JSON demandé ; il ne crée ni ne met à jour
  `registry.json`. Il sert donc à régénérer une date déjà enregistrée, pas à
  publier seul une nouvelle date.
- Une option mal formée, une date absente, une valeur non numérique ou une tranche
  de moins de 28 éléments est signalée sous `Skipped eclipses`, exclue de la
  sortie et provoque un code de retour non nul. L'outil ne complète ni ne corrige
  silencieusement les sources.

La génération est idempotente sur le contenu astronomique : à sources identiques,
les dates, offsets, métadonnées de source et 28 éléments sont identiques, et les
fichiers de même nom sont remplacés. Elle n'est pas identique octet pour octet car
`generated_utc` est renouvelé à chaque exécution. `build-all` ne supprime pas
d'éventuels fichiers obsolètes déjà présents dans `data/eclipses/` ; seul son
`registry.json` définit les datasets chargeables.

### Utiliser et valider le moteur Python

Le point d'entrée utilisateur est `scripts/eclipse_calculator_py.py`. Il charge
uniquement une date déclarée dans `data/eclipses/registry.json`, calcule les
circonstances locales, puis produit le même document de configuration destiné au
trigger que le flux historique :

```bash
python3 scripts/eclipse_calculator_py.py \
  --lat 25.6872 --lon 32.6396 --alt 80 --tz 2 \
  --eclipse 2027-08-02 --output todayeclipse.json
```

Sans `--output`, le résultat est écrit sous
`data/eclipses/out/<date>_<latitude>_<longitude>.json`. `--tz` est le décalage
UTC total en heures, heure d'été comprise ; les champs `C1` à `C4` et `TMAX`
restent en UTC et leurs variantes `_local` appliquent ce décalage.

La procédure de non-régression est :

```bash
~/dev/eclipse-ai/.venv/bin/python -m pytest -q \
  tests/test_eclipse_dataset_builder.py \
  tests/test_eclipse_datasets.py \
  tests/test_eclipse_loader.py \
  tests/test_eclipse_observer.py \
  tests/test_eclipse_compute.py \
  tests/test_eclipse_calculator_py.py

ECLIPSE_DIFF_DATES=2026-08-12,2027-08-02 \
  ~/dev/eclipse-ai/.venv/bin/python -m pytest -q \
  tests/test_diff_jubier_vs_python.py

~/dev/eclipse-ai/.venv/bin/python -m pytest -q
```

Sans `ECLIPSE_DIFF_DATES`, le test différentiel couvre toutes les dates du registre,
à Louxor, Madrid et Sydney. La politique de tolérance par rapport au JS Jubier est
de `0,5 s` par contact (écart circulaire sur 24 h), `1e-6` pour la magnitude et le
rapport Lune/Soleil, et `0,05°` pour l'altitude de chaque contact. Le type d'éclipse
et la présence ou l'absence de chaque contact doivent être strictement identiques.
Le test est explicitement ignoré si Playwright ou un Chromium lançable manque ;
un tel skip ne constitue donc pas une validation différentielle réussie. Après les
tests automatisés, vérifier dans l'IHM qu'un JSON produit se charge et s'affiche
comme avant, sans changement de route, de champ ou de séquencement.

Pour les responsabilités internes du chargeur, du calcul et du différentiel, voir
`ARCHITECTURE_V6.md`, addendum « moteur d'éclipse Python ».

---

*Projet SolarEclipse — Raspberry Pi 3B — 2026/2027*

## 🛰️ Architecture GPS — v5.26

Le GPS n'est plus conçu comme un matériel codé en dur dans le backend. Les sources GPS sont derrière le contrat `GpsPlugin` :

```text
GPS Service (futur)
        │
    GpsPlugin
        ├── SerialNmeaGps   → dongle USB/TTL NMEA (BU-353N5, autre)
        └── GpsdPlugin      → gpsd
```

### Plugin `serial_nmea`

Configuration typique du GlobalSat BU-353N5 :

```json
{
  "plugin": "serial_nmea",
  "port": null,
  "vid": "067b",
  "pid": "2303",
  "baudrate": 4800,
  "timeout": 1.0
}
```

Le VID/PID identifie le matériel, mais le plugin reste générique : un autre dongle NMEA peut être utilisé avec un autre VID/PID, ou avec un port explicite.

Le plugin normalise les informations `position`, `heure UTC`, `altitude`, `satellites`, `HDOP` et vitesse. Il ne modifie pas l'heure système : la synchronisation système reste une responsabilité distincte.

### Plugin `gpsd`

Le plugin `gpsd` permet d'utiliser un GPS déjà exposé par `gpsd`, sans dépendre directement du port série.

Les deux plugins sont importables sans imposer la présence de leurs dépendances optionnelles (`pyserial` / module Python `gps`) tant que le plugin concerné n'est pas utilisé.

## Backend v6.0

La version 6.0 introduit un backend séparé de Flask : état/persistance, journalisation, acquisition GPS ponctuelle, validation et cycle de vie du trigger sont dans `backend/`. Voir `ARCHITECTURE_V6.md`.

### v6.1
La simulation backend est maintenant explicitement séparée du démarrage réel via `POST /api/trigger/simulate` (JSON optionnel `{"speed":60}`). Le mode simulation n'accède jamais à la caméra réelle.


## Déploiement applicatif courant

Les mises à jour applicatives sont déployées depuis la VM avec `tools/deploy-prod.sh`.

Pour une installation complète ou une modification des dépendances système,
systemd, nginx, udev, gpsd ou chrony, utiliser `install/install_solareclipse.sh`.



## v6.5
- Suppression définitive de l'économie USB héritée du Nikon D850. La caméra reste connectée pendant toute la séquence.
- Packaging corrigé : l'archive contient un dossier racine `solareclipse_package/` et exclut les caches Python/pytest.


## Configuration runtime
Les fichiers JSON utilisés par le backend sont déployés dans `~/python_solareclipsetrigger/configs/`. Le backend ne dépend plus de `~/configs`.

## v7.1 — circonstances précises et dry-run de qualification

Les circonstances utilisent désormais `_date` + heures UTC indépendantes `HH:MM:SS.sss`, accompagnées de `_circumstances_location` (GPS + altitude de calcul). Le dry-run ×1 translate la timeline sur maintenant mais exécute exactement le même moteur et le même matériel que le mode réel. Voir `TIME_MODEL_V71.md`.
