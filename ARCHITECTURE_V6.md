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
