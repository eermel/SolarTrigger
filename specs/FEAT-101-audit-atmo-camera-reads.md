# FEAT-101 — Audit Atmos et lectures caméra

## Objet et sources

Ce rapport décrit le comportement présent, sans proposer de modification de
timing, de séquencement ou d'accès au matériel. Il est traçable aux sources
suivantes :

- `backend/atmo.py` : `facteur_atmospherique`, `interpolate_altitude` ;
- `scripts/eclipse_trigger.py` : `_capture_intent`,
  `_sim_capture_speed_list` ;
- `services/camera_service.py` : `_normalized_speed_plan`,
  `prepare_capture`, `shoot_speed_list`, `get_battery_level` ;
- `plugins/camera/sony.py` : `set_speed_blocking`, `_drain_frames`
  (`camera.wait_for_event`), `_settle_idle`, `init_settings` ;
- `flask_app/app.py` : `_get_camera_status`, `/api/status`,
  `/api/camera/probe`, `/api/camera/sync_time` ;
- `flask_app/templates/index.html` : appels frontend associés.

## REQ-001 — Algorithme Atmos

### Facteur atmosphérique

`facteur_atmospherique(h_deg, H_m)` convertit d'abord ses deux arguments en
flottants et rejette les valeurs non numériques. Son calcul interne `F(h, H)`
est le suivant :

1. Pour une altitude solaire géométrique `h > 0°`, il pose
   `cosz = sin(h)` puis calcule la masse d'air
   `air_mass = 1 / (cosz + 0.025 × exp(-11 × cosz))`. Pour `h <= 0°`, la
   masse d'air est bornée à `40`.
2. Les trois extinctions sont : ozone `Aoz = 0.016`, Rayleigh
   `Aray = 0.1451 × exp(-(H/1000)/7.996)` et aérosols
   `Aaer = 0.120 × exp(-(H/1000)/1.5)`.
3. L'extinction totale vaut `(Aoz + Aray + Aaer) × air_mass`, et
   `F(h, H) = 2.512 ** extinction`.
4. La valeur retournée est sans dimension et normalisée au zénith, au niveau
   de la mer : `F(h, H) / F(90°, 0 m)`.

Le facteur est donc une compensation multiplicative de durée d'exposition,
pas une mesure lue sur le boîtier.

### Interpolation de l'altitude solaire

`interpolate_altitude(t, timeline, alts)` exige les cinq dates `datetime`
`C1`, `C2`, `TMAX`, `C3`, `C4` et les cinq valeurs `C1_alt_deg`,
`C2_alt_deg`, `TMAX_alt_deg`, `C3_alt_deg`, `C4_alt_deg`. Il parcourt, dans
cet ordre, les segments fermés `C1→C2`, `C2→TMAX`, `TMAX→C3`, `C3→C4`.
Sur le segment contenant `t`, l'altitude est l'interpolation linéaire
`h0 + ((t-t0)/(t1-t0)) × (h1-h0)`. Un segment de durée nulle ou négative
retourne sa première altitude. Hors segments, la fonction borne à l'altitude
de C1 avant C1 et à celle de C4 dans tous les autres cas, notamment après C4.

### Activation, prérequis et intervention dans le plan

L'activation vient exclusivement de
`capture_canonical.exposure_correction.atmospheric_attenuation_enabled`.
Quand elle est active, `_capture_intent` exige :

- l'altitude de l'observateur `altitude_m` ;
- les dates C1/C2/TMAX/C3/C4 de la timeline ;
- les altitudes géométriques persistées C1/C2/TMAX/C3/C4 ;
- l'heure cible de la capture.

L'extension Atmos n'est appliquée que si le plan est régulier, c'est-à-dire
si ses écarts sont approximativement constants en EV (ou si le plan est déjà
fourni sous forme de bornes et d'un pas). Une liste explicite irrégulière reste
inchangée : aucune exposition intermédiaire n'est inventée.

`_capture_intent` interpole l'altitude géométrique à l'heure cible, calcule le
facteur, puis multiplie la durée de la vitesse la plus lente par ce facteur.
À partir de la borne lente existante, il avance par multiplicateurs
`2 ** step_ev`. La nouvelle borne est inclusive : si la cible dépasse la
borne courante, le premier pas égal ou supérieur à la cible est retenu.

- Pour une liste explicite régulière, les vitesses calculées sont ajoutées à
  la liste et formatées par `_format_seconds_as_speed` (durée brute à partir
  de 1 s, notation `1/x` sous 1 s).
- Pour un plan décrit par bornes, `shutter_min` est remplacé par la nouvelle
  borne lente formatée. La voie matérielle reçoit ainsi la borne étendue dans
  le `CaptureIntent`. L'ancienne API `shoot_speed_list` représente le même
  mécanisme matériel sous la forme de `slowest_override_seconds` : elle ne
  l'accepte que pour un plan régulier, refuse tout raccourcissement, puis
  transmet les bornes au plugin.
- `_sim_capture_speed_list`, voie de simulation historique, applique de même
  une extension inclusive par `step_ev`, ajoute une liste explicite de
  vitesses formatées, refuse un plan irrégulier ou une borne plus courte, et
  ne réalise aucun appel gphoto2.

La normalisation par `services.camera_service._normalized_speed_plan` trie les
vitesses de la plus rapide à la plus lente et retire automatiquement les
doublons de même représentation textuelle avant de déterminer le pas médian
et la régularité. Elle ne fusionne pas nécessairement deux écritures textuelles
différentes représentant la même durée (par exemple `0.5` et `1/2`).

## REQ-002 — Inventaire des lectures caméra

### Lectures automatiques

`GET /api/status` appelle systématiquement `_get_camera_status`. Celui-ci
instancie `gp.Camera()`, exécute `camera.init()`, lit le modèle et tente
`camera.get_config().get_child_by_name("batterylevel").get_value()`, puis
appelle `camera.exit()` en cas de succès. Le résultat `connected`, `brand`,
`model`, `battery` met à jour la section `camera` de l'état, puis `_save_state`
persiste notamment cette section dans `state.json`.

Le frontend appelle `/api/status` à la connexion/reconnexion et via
`loadCameraStatus` toutes les dix secondes. Il existe donc une sonde
automatique globale modèle/batterie. En revanche, le simple affichage de
l'onglet Camera ne déclenche aucune route dédiée lisant ISO, ouverture,
vitesse, mode de prise de vue ou stockage ; aucune de ces propriétés n'est
lue automatiquement par cette vue.

### Lectures et actions manuelles

- `POST /api/camera/probe`, déclenché par le bouton opérateur, ouvre une
  connexion temporaire, lit marque/modèle/batterie par le même chemin, ferme
  immédiatement la caméra et persiste le résultat avec `connected = false`.
- `POST /api/camera/sync_time`, déclenché par l'opérateur, connecte le service
  caméra si nécessaire et effectue une synchronisation via le plugin ; ce
  n'est pas une lecture au chargement de l'onglet.
- `CameraService.get_battery_level()` délègue à
  `plugin.get_battery_level()` lorsqu'un plugin est connecté et retourne
  `None` sinon. C'est une capacité du service, distincte de la sonde directe
  de `/api/status`.

## REQ-003 — Appels potentiellement bloquants et invariants

### Sony pendant le déclenchement

Sur Sony, l'occupation du boîtier peut rendre `capturemode` et surtout
`shutterspeed` temporairement « read only ». `set_speed_blocking` tente
l'écriture de `shutterspeed`, attend 50 ms après chaque erreur read-only et
réessaie pendant au plus 6 s (ou jusqu'à la deadline). Il ne valide pas
l'écriture par une relecture, celle-ci étant considérée non fiable.

Après le maintien d'obturateur d'un bracket, `_drain_frames` appelle
`camera.wait_for_event(200)` et compte les seuls événements `FILE_ADDED` ; il
ignore le caractère non fiable de `CAPTURE_COMPLETE` et s'arrête au nombre
attendu, après un silence dépendant de la pose lente, ou au timeout global.
`_settle_idle` continue ensuite à drainer par `wait_for_event(100)` jusqu'à un
silence de plus de 0,3 s, dans une fenêtre maximale de 2 s. `init_settings` et
les transitions d'exposition écrivent plusieurs propriétés, dont
`capturemode`; ces écritures peuvent également échouer ou devenir read-only
si le boîtier est occupé.

Ces attentes Sony appartiennent à la préparation/exécution des captures ;
`/api/status` ne les appelle pas. Elles ne doivent pas être confondues avec la
sonde gphoto2 du statut.

### Sonde gphoto2

Les lectures de batterie et de modèle de `/api/status` et du probe impliquent
`camera.init()`, puis `get_config()` pour la batterie, et `camera.exit()`.
Ce sont des appels synchrones susceptibles de bloquer de plusieurs centaines
de millisecondes à plusieurs secondes selon le boîtier et son état, en
particulier avec certains Sony. Le code actuel ne les déporte ni dans un
worker ni derrière un cache.

### Invariants de sûreté

- Le dry-run ne recalcule pas silencieusement les circonstances : il translate
  la timeline complète et conserve les intervalles et les circonstances.
- Le moteur utilise une seule abstraction d'horloge ; les deadlines murales
  sont converties une fois en échéances monotoniques avant les plugins.
- Atmos consomme les altitudes géométriques persistées des cinq contacts et
  l'altitude observateur. Il ne les relit pas depuis le boîtier et ne doit pas
  les substituer par des circonstances recalculées implicitement.
