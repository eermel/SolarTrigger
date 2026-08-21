# SolarEclipse backend v6.0

La v6 sépare la couche HTTP, l'état applicatif, les services matériels et l'orchestration de la séquence.

```text
IHM
 │ HTTP / SocketIO
 ▼
flask_app/app.py              adaptateur uniquement
 │
 ├── backend/StateStore       état/persistance thread-safe
 ├── backend/EventLog         journal + diffusion événements
 ├── backend/GpsController    acquisition opérateur ponctuelle
 └── backend/TriggerService   validation + cycle de vie processus
          │
          ▼
 scripts/eclipse_trigger.py   orchestrateur de séquence photo
          │
          ├── backend/RuntimeClock
          ├── backend/TriggerWatchdog
          └── gphoto2 historique (à migrer vers CameraService après validation v6)

services/
 └── gps_service.py           service applicatif matériel
plugins/
 ├── camera/
 ├── focuser/
 ├── gps/
 └── mount/
```

## Règles

- Flask ne possède plus directement le processus trigger ni la logique GPS.
- `StateStore` est la source de vérité backend pour l'état persistant/runtime.
- Le GPS applicatif est lancé uniquement sur demande opérateur; `gpsd/chrony` restent gérés par Linux.
- `TriggerService` valide les préconditions et possède start/stop/totality-only.
- `eclipse_trigger.py` conserve pour v6.0 la logique photo gphoto2 éprouvée afin de ne pas modifier les timings le même jour que le refactor architectural. Son horloge virtuelle et son watchdog sont toutefois externalisés.
- Les plugins caméra/focuser/monture restent indépendants et seront raccordés aux services sans changer l'API IHM.

## v6.1 — Simulation sûre

- `/api/trigger/start` reste réservé au déclenchement réel.
- `/api/trigger/simulate` lance explicitement `eclipse_trigger.py --simulate --speed N`.
- En simulation, aucun accès matériel caméra n'est effectué : pas de détection, configuration, batterie, reconnexion USB ou déclenchement réel.
- Les threads d'alertes/lecture audio sont arrêtés et joints avant fermeture de `pygame.mixer`.
- Le facteur de simulation est borné entre 1 et 1000.
- Les configurations runtime sont déployées dans `~/python_solareclipsetrigger/configs/`, qui est la source canonique du backend.

## v6.2
- Simulation backend indépendante de la synchronisation GPS.
- Correction du comptage estimé des brackets (premier tir immédiat compté).
- Installateur complet : restart du service Flask après mise à jour.
- Permissions exécutables des scripts d installation conservées dans le ZIP.

## Mise à jour rapide — v6.3

Depuis la v6.3, `install/update_files.sh` déploie systématiquement les trois couches
applicatives utilisées par Flask et le trigger : `backend/`, `services/` et `plugins/`.
Cela évite un mélange de versions entre `flask_app/app.py` et le backend installé dans
`~/python_solareclipsetrigger`.

Le script copie aussi `VERSION` dans les deux installations actives :

- `~/python_solareclipsetrigger/VERSION`
- `~/flaskapp_solareclipsetrigger/VERSION`

Il compile les fichiers Python critiques avant de redémarrer `solareclipse.service`,
puis vérifie que le service est réellement actif.

## v6.4 — intégration CameraPlugin dans le moteur

Le moteur `eclipse_trigger.py` ne pilote plus directement les paramètres PTP de
la caméra. Le chemin réel est désormais :

`eclipse_trigger -> CameraService -> CameraPlugin -> Sony/Nikon -> gphoto2`

Le moteur ne contient plus `shutterspeed2`, `trigger_capture()` ni de nom de
paramètre propre à une marque. `totality_only.py` suit le même chemin.

Les profils `camera_*.json` ne définissent que l'exposition (ISO, ouverture,
plage de vitesses). Les timings (`interval_partial`, `duree_diamond_ring`, etc.)
appartiennent à la séquence `todayeclipse.json`. Ordre de priorité : valeurs par
défaut < profil caméra < séquence éclipse/debug < CLI.


## v6.5
- Suppression définitive de l'économie USB héritée du Nikon D850. La caméra reste connectée pendant toute la séquence.
- Packaging corrigé : l'archive contient un dossier racine `solareclipse_package/` et exclut les caches Python/pytest.

## v7.0 — autorité temporelle

Le trigger réel utilise une horloge UTC ancrée une fois au lancement puis avancée par `time.monotonic()`. Les plugins caméra reçoivent également des deadlines monotonic. L'IHM reçoit l'epoch UTC de la Pi et utilise `performance.now()` uniquement pour interpoler entre deux mises à jour. Voir `TIME_AUDIT_V7.md`.

## Addendum — moteur d'éclipse Python et migration des datasets

Cette migration est isolée du moteur de déclenchement et des services matériels.
Elle ajoute une chaîne de calcul locale sans modifier Flask, les routes HTTP/
SocketIO, les templates, le JavaScript de l'IHM, les formats consommés par l'IHM,
ni le séquencement des phases :

```text
jubier_files/index.html + SolarEclipseTimerSVG_VML.js
                    │ génération hors exécution
                    ▼
scripts/eclipse_dataset_builder.py
                    │
                    ▼
data/eclipses/registry.json + <date>.json
                    │ lecture seule, date enregistrée uniquement
                    ▼
backend/eclipse_engine/loader.py
                    │ dataset complet et inchangé
                    ▼
backend/eclipse_engine/compute.py
                    │ circonstances locales
                    ▼
scripts/eclipse_calculator_py.py
                    │ JSON compatible avec le trigger existant
                    ▼
data/eclipses/out/ (ou --output)
```

### Chargement et calcul

`backend/eclipse_engine/loader.py` considère
`data/eclipses/registry.json` comme la liste d'autorité. Il refuse une date absente,
un JSON invalide et tout nom de fichier contenant un chemin ; il retourne le
dataset enregistré sans le transformer. Chaque dataset conserve la valeur et
l'offset Jubier, la provenance de l'option HTML et les 28 éléments besseliens
nommés.

`backend/eclipse_engine/compute.py` accepte le dataset complet ou exactement le
mapping de ces 28 éléments. Il rejette les clés supplémentaires/manquantes et les
valeurs non numériques, prépare les constantes de l'observateur dans
`backend/eclipse_engine/observer.py`, puis porte les itérations du JS Jubier pour
le maximum et les contacts externes/internes. Il retourne le type local, magnitude,
rapport Lune/Soleil, durée, altitude et heures UTC/locales. Le module ne lit aucun
matériel, ne modifie aucun état global et n'accède pas au réseau.

`scripts/eclipse_calculator_py.py` est l'adaptateur de sortie : il valide latitude,
longitude, altitude, fuseau et date, appelle le chargeur puis le calculateur, et
construit le schéma historique destiné au trigger. Les éclipses partielles, sans
contacts internes, placent comme auparavant C2 et C3 à TMAX pour conserver un
document complet. Cette compatibilité de sortie est la frontière de
non-régression ; aucun changement d'IHM n'est requis ou autorisé par la migration.

### Différentiel et critère de validation

`tests/test_diff_jubier_vs_python.py` exécute le moteur Python et le JS local
historique `scripts/eclipse_calculator_jubier.py` sur les mêmes datasets et
observateurs. Par défaut, toutes les dates de `registry.json` sont testées à
Louxor, Madrid et Sydney ; `ECLIPSE_DIFF_DATES`, liste de dates ISO séparées par
des virgules, permet un contrôle ciblé.

Les deux moteurs doivent produire exactement le même type d'éclipse et la même
disponibilité de C1, C2, TMAX, C3 et C4. Les écarts maximaux admis sont :

| Mesure | Tolérance | Règle |
|---|---:|---|
| Heure de chaque contact | 0,5 s | plus petit écart sur un cycle de 24 h |
| Magnitude | 1e-6 | différence absolue |
| Rapport Lune/Soleil | 1e-6 | différence absolue |
| Altitude de chaque contact | 0,05° | différence absolue |

Commande exhaustive depuis la racine :

```bash
~/dev/eclipse-ai/.venv/bin/python -m pytest -q \
  tests/test_diff_jubier_vs_python.py
```

Playwright et un Chromium lançable ne servent qu'à cet oracle différentiel ; leur
installation détaillée reste hors de cet addendum. Si l'un manque, pytest marque
le test comme ignoré avec sa raison : ce résultat doit être signalé et ne remplace
pas un passage différentiel. La validation complète enchaîne les tests ciblés du
builder/chargeur/calculateur, ce différentiel, la suite pytest du dépôt, puis un
contrôle manuel de chargement et d'affichage dans l'IHM existante. Tout écart hors
des tolérances doit être expliqué et corrigé dans la chaîne dataset/calcul ; il ne
doit pas être masqué par une modification de l'IHM, du trigger ou des seuils.
