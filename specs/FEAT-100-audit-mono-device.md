# FEAT-100 — Audit mono-device

## Objet et périmètre

Ce document décrit l'architecture mono-device existante à la baseline `64966ec99fe34c9870af0f8f52528d2a86df205c`. Il s'agit d'un relevé du code, pas d'une proposition de refonte : les API actuelles, le séquencement et le comportement matériel ne sont pas modifiés.

## Inventaire

### Arborescence focalisée

- `scripts/` : moteur d'exécution de l'éclipse ; `scripts/eclipse_trigger.py` contient le contrat de capture utilisé par le séquenceur, son équivalent de simulation et la connexion à la caméra.
- `services/` : frontières applicatives vers un périphérique sélectionné ; `camera_service.py`, `mount_service.py` et `focuser_service.py` portent respectivement les façades caméra, monture et EAF.
- `plugins/camera/` : contrat générique dans `base.py`, implémentations `sony.py` et `nikon.py`, et composition des rafales Sony dans `sony_planner.py`.
- `plugins/mount/` : contrat générique dans `base.py`, implémentations `indi_plugin.py` et `onstep_plugin.py`.
- `plugins/focuser/` : contrat générique dans `base.py` et implémentation ZWO EAF dans `zwo_plugin.py`.
- `flask_app/` : façade HTTP et SocketIO ; `app.py` construit l'état et les services uniques, puis expose les routes de contrôle.
- `backend/` : état persistant et logique transverse ; `devices.py` définit les catégories, normalise une sélection et détecte les périphériques.
- `configs/` et `data/` : configuration de capture/circonstances et données d'éclipses consommées par l'application.
- `tests/` : tests backend, services, plugins et intégration.
- `specs/` : rapports et spécifications d'architecture, dont le présent audit.

## Hypothèses et points mono-device

### Une sélection par catégorie

`backend/devices.py:17` fixe les quatre catégories `camera`, `gps`, `focuser` et `mount`. `normalize_selection()` (`backend/devices.py:60-69`) transforme une valeur ou un objet en **une** sélection `{plugin, active}` ; `detect_camera()`, `detect_focuser()`, `detect_mount()` et `detect_all()` (`backend/devices.py:72-130`) produisent une suggestion unique par catégorie, sans notion de parc ou de RIG.

Dans `flask_app/app.py:314-324`, `_devices_snapshot()` lit une seule section `StateStore.devices` et assure une seule entrée par catégorie. `POST /api/devices` boucle sur ces catégories puis persiste, pour chacune, un seul couple `plugin`/`active` (`flask_app/app.py:446-480`). Il n'existe ni liste de rigs, ni clé `rig_id`, ni sélection multiple au sein d'une catégorie.

### Services et caméra uniques

- `_state_store` est construit une fois au niveau module (`flask_app/app.py:245`) et sa section `devices` est commune à toutes les requêtes.
- `_focuser_service` et `_mount_service` sont des singletons de processus, construits une fois dans `flask_app/app.py:248-251`. Chacun conserve un unique `_plugin` et un unique `_plugin_id` (`services/focuser_service.py:31-32`, `services/mount_service.py:33-34`).
- Le moteur crée un seul `camera_service` pour une exécution : `_SimulationCameraService` en simulation ou un unique `CameraService`, ensuite connecté à une caméra et à un plugin (`scripts/eclipse_trigger.py:1576-1610`). `CameraService.connect()` possède un seul `camera`, un seul `model` et un seul `plugin` (`services/camera_service.py:75-108`). La sélection réelle du plugin est donc celle d'une caméra unique détectée par `get_camera_model()`/`load_plugin()`, et non un routage vers plusieurs caméras.

### API et événements non adressés

Toutes les routes monture sont globales (`/api/mount/status`, `/tracking/*`, `/speed`, `/slew/*`, `/home`, `flask_app/app.py:710-861`) et toutes les routes EAF le sont également (`/api/focuser/status`, `/mode`, `/home`, `/stop`, `/move_to`, `/step`, `/jog/*`, `/set_step`, `flask_app/app.py:547-704`). Ni le chemin, ni le corps imposé, ni l'appel au service ne portent de `rig_id`.

Les événements SocketIO sont eux aussi globaux : `focuser_update` (`flask_app/app.py:402-406`), `gps_update` (`flask_app/app.py:918-924,978`), et les nombreuses émissions `status_update` (notamment `flask_app/app.py:923,1238,1793,1912`) ne contiennent pas d'identifiant de RIG. `trigger_phase` est généré par `TriggerService`, puis relayé tel quel par le callback générique `_emit_trigger(event, payload)` de `flask_app/app.py:1716-1721`; ce relais n'ajoute aucun `rig_id`. Les clients reçoivent donc un état et une phase uniques pour l'application entière.

## Chaîne photo

1. Le séquenceur de `scripts/eclipse_trigger.py` construit un `CaptureIntent`, description indépendante de la marque (`services/camera_service.py:20-33`), puis obtient un `PreparedCapture`, plan opaque accompagné d'estimations (`services/camera_service.py:35-44`). `_SimulationCameraService` reproduit les méthodes `prepare_capture()` et `trigger_prepared()` sans obturateur (`scripts/eclipse_trigger.py:966-1000`).
2. Les boucles modernes préparent en avance par `camera_service.prepare_capture(intent)`, attendent la cible, puis exécutent `camera_service.trigger_prepared(prepared, deadline=...)` (`scripts/eclipse_trigger.py:1050-1307`). L'adaptateur historique `capture_speed_list()` passe, lui, par `CameraService.shoot_speed_list()` (`scripts/eclipse_trigger.py:1319-1355`).
3. `CameraService.prepare_capture()` normalise bornes ou liste explicite puis délègue au plugin actif ; `trigger_prepared()` convertit si nécessaire la deadline UTC en deadline monotone puis délègue (`services/camera_service.py:160-212`). `shoot_speed_list()` délègue une plage régulière à `plugin.shoot_speeds()` ou préserve une liste irrégulière via des `plugin.shoot_single()` successifs (`services/camera_service.py:242-315`).
4. Le contrat par défaut `CameraPlugin.prepare_capture()`/`trigger_prepared()` transforme le plan en appel `shoot_speeds()` ou en singles (`plugins/camera/base.py:161-236`). C'est le repli utilisé par les plugins qui ne spécialisent pas la préparation.
5. Divergence Sony : `SonyPlugin.prepare_capture()` appelle `sony_planner.plan()` et produit une séquence de `Bracket`/`SinglePhoto` avec durées estimées (`plugins/camera/sony.py:223-253`; `plugins/camera/sony_planner.py:52-75,116-125,166-205`). À l'exécution, `_execute_sequence()` peut adapter le bracket à la deadline (`plugins/camera/sony.py:263-329`). `_fire_bracket()` sélectionne le mode bracket, maintient `bulb=1`, compte les événements `FILE_ADDED`, relâche avec `bulb=0`, puis attend le repos avec `_settle_idle()` (`plugins/camera/sony.py:101-140,171-188`). Les singles Sony utilisent `trigger_capture()`, drainent un `FILE_ADDED`, puis font aussi le settle (`plugins/camera/sony.py:190-202`).
6. Divergence Nikon : le contrat générique aboutit à `NikonBasePlugin.shoot_speeds()`, qui calcule les vitesses puis, pour chaque photo, règle la vitesse avec `_set_speed()` et déclenche `_fire()`/`camera.trigger_capture()` ; la deadline est vérifiée photo par photo (`plugins/camera/nikon.py:91-113,136-153`). Il n'y a ni rafale bracket pilotée par maintien `bulb`, ni comptage collectif de `FILE_ADDED`, ni phase de settle équivalente dans cette implémentation.

**Dernier point commun avant divergence caméra :** l'instance unique de `CameraService`, précisément la délégation à `self.plugin.prepare_capture()` ou `self.plugin.trigger_prepared()` (`services/camera_service.py:198,212`). Sur le chemin historique, le dernier point commun est la délégation de `shoot_speed_list()` vers l'interface `CameraPlugin` (`services/camera_service.py:280-315`). À partir de ce plugin sélectionné, Sony planifie et exécute des brackets spécialisés tandis que Nikon suit le comportement générique photo-par-photo.

## Chaîne monture

1. Les routes `/api/mount/*` valident activité, payload et conflits, puis appellent l'unique `_mount_service` : statut, mode/start/stop tracking, vitesse, début de slew, home asynchrone et stop (`flask_app/app.py:710-861`).
2. `MountService` sérialise l'état par `_lock` et sépare aussi l'accès matériel d'arrêt via `_plugin_access_lock` (`services/mount_service.py:30-31,430-447`). `_selection()` lit l'unique `devices.mount`; `_plugin_for_operation()` ferme un ancien plugin, charge le plugin sélectionné, le connecte, pousse éventuellement la position GPS et initialise l'état de tracking (`services/mount_service.py:42-112`).
3. Les méthodes de service basculent le tracking seulement si les capacités annoncent `toggle`, valident vitesse et direction, puis appellent le plugin (`services/mount_service.py:277-385`). `home_start()` marque `_homing`, invalide les générations précédentes et lance `plugin.go_home()` dans un thread daemon avec annulation si l'implémentation l'accepte (`services/mount_service.py:387-426`). `stop()` annule logiquement le homing et tente de stopper le mouvement sans modifier le tracking (`services/mount_service.py:428-467`).
4. `MountPlugin` définit le contrat commun de connexion, statut, tracking, mouvement, vitesse, home et arrêt d'urgence (`plugins/mount/base.py:43-156`). `IndiMount` traduit ce contrat vers les propriétés INDI et implémente notamment tracking, mouvement et home (`plugins/mount/indi_plugin.py:33-455`). `OnStepMount` le traduit vers le contrôleur OnStep, y compris tracking, mouvements, home/recentrage et arrêt d'urgence (`plugins/mount/onstep_plugin.py:38-268`).

**Dernier point commun avant divergence monture :** `MountService._plugin_for_operation()` (`services/mount_service.py:61-112`) retourne l'unique objet conforme à `MountPlugin`. Chaque opération du service appelle ensuite cette interface commune ; la divergence de protocole commence dans `IndiMount` ou `OnStepMount`.

## Chaîne EAF / focuser

1. Les routes `/api/focuser/*` vérifient que la catégorie est active et, pour les mouvements, qu'aucun trigger ou mouvement incompatible n'est actif. Elles appellent l'unique `_focuser_service`, puis `_focuser_result()` publie le résultat global `focuser_update` (`flask_app/app.py:370-406,547-704`).
2. `FocuserService` maintient le mode backend `slow`/`fast`, les pas associés et l'état du mouvement sous un `RLock` (`services/focuser_service.py:25-38`). `_load_settings()`, `_ensure_settings_current()` et `_persist_settings()` restaurent, réinitialisent après expiration et persistent `mode`, `slow_step`, `fast_step` dans `StateStore.focuser_settings` (`services/focuser_service.py:44-96`).
3. `_selection()` lit l'unique `devices.focuser`; `_plugin_for_operation()` charge et connecte un seul plugin (`services/focuser_service.py:127-157`). `move_to()`/`move_relative()` délèguent les déplacements. `start_jog()` ignore le mode fourni par les anciens appelants et traduit le mode backend en `coarse` ou `fine`; `stop_jog()` appelle `stop_continuous()` (`services/focuser_service.py:211-264`). `set_step()` met à jour le plugin, les pas de service et leur persistance (`services/focuser_service.py:279-289`).
4. `FocuserPlugin` définit le contrat commun de statut, position, pas, déplacements et mouvement continu (`plugins/focuser/base.py:40-109`). `ZwoFocuser` l'implémente sur le SDK EAF ; son jog repose sur une boucle de maintien et `start_continuous()`/`stop_continuous()` (`plugins/focuser/zwo_plugin.py:31-168`).

**Dernier point commun avant divergence focuser :** `FocuserService._plugin_for_operation()` (`services/focuser_service.py:140-157`) retourne l'unique `FocuserPlugin`. Les méthodes `move_*`, `start_continuous`, `stop_continuous`, `set_step` et `status` constituent ensuite la frontière commune ; toute divergence matérielle commence dans l'implémentation du plugin, actuellement `ZwoFocuser`.

## Synthèse des derniers points communs

| Chaîne | Dernier point commun | Divergence actuelle |
|---|---|---|
| Caméra | `CameraService` délègue à l'unique `CameraPlugin` | Sony : planner/brackets/bulb/`FILE_ADDED`/settle ; Nikon : photo-par-photo |
| Monture | `MountService._plugin_for_operation()` retourne un `MountPlugin` | transport et capacités `IndiMount` ou `OnStepMount` |
| EAF | `FocuserService._plugin_for_operation()` retourne un `FocuserPlugin` | implémentation matérielle `ZwoFocuser` |

Ces trois frontières sont mono-device : elles mémorisent ou utilisent un seul plugin actif et ne reçoivent aucun identifiant permettant de choisir un RIG à chaque opération.
