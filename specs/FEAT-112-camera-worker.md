# FEAT-112 — Contrat développeur de `CameraWorker`

## Objet

`CameraWorker` est la façade synchrone qui confie toutes les opérations d'une
caméra à un `GenericWorker` dédié. Une instance appartient à un seul RIG et
conserve un unique `CameraService` pendant toute sa durée de vie.

## API

Le constructeur accepte `rig_id`, ainsi que les dépendances optionnelles
`service_factory`, `log_fn` et `clock`. Il transmet à `GenericWorker` la
politique d'arrêt `shutdown_policy` (`"drain"` par défaut) et la limite
`max_queue_size`.

- Cycle de vie : `start()`, `stop(timeout=None)` et la propriété `running`.
- Connexion et configuration : `connect()`, `init_settings(...)`,
  `set_exposure_settings(...)` et `apply_phase_settings(...)`.
- Capture : `prepare_capture(intent)`, `trigger_prepared(prepared,
  deadline=None)` et `shoot_speed_list(...)`.
- Lecture et maintenance : `get_battery_level()`, `sync_datetime(ref)` et
  `probe_info()` ; ce dernier connecte la caméra si nécessaire et renvoie son
  modèle, le nom du plugin et le niveau de batterie.
- `test_photo(...)` est une façade de test vers `shoot_speed_list(...)`.

Ces méthodes soumettent un job puis attendent son `Future` : leur contrat est
synchrone pour l'appelant, tandis que l'accès au service s'effectue sur le
thread du worker. Le worker doit donc être démarré avant leur utilisation. Les
valeurs de retour et exceptions de `CameraService` sont propagées à l'appelant.

## Cycle de vie et service paresseux

`start()` démarre le thread persistant `camera-worker-r<rig_id>` selon le
contrat de `GenericWorker`. Le `CameraService` n'est pas construit à
l'instanciation ni au démarrage : `service_factory` est appelé lors du premier
job qui requiert le service, dans le thread du worker. Sans factory explicite,
le worker crée un `CameraService` avec `log_fn` et `clock`.

`stop()` applique la politique d'arrêt de `GenericWorker`. À la fin de la
boucle, le service créé est fermé au plus une fois. Une instance arrêtée ne
peut pas être redémarrée ; après le début de l'arrêt, elle n'accepte plus
d'opération.

## Invariants de threading par RIG

- Une instance utilise une seule file et un seul thread : ses opérations caméra
  s'exécutent une par une, dans l'ordre de soumission.
- Le service est créé, utilisé et fermé via le cycle de vie de ce worker ; il
  n'est pas exposé aux appelants.
- Chaque RIG doit posséder sa propre instance. Les workers de RIG distincts ont
  des files et des threads indépendants : une opération lente sur un RIG ne
  bloque pas les autres.
- La séquentialité est garantie au sein d'une instance, mais aucun ordre global
  n'est garanti entre plusieurs RIG.

## Limites actuelles

`CameraWorker` n'est pas encore intégré au séquenceur. Cette spécification ne
définit ni branchement au cycle des phases, ni API ou interface utilisateur, ni
politique d'orchestration globale des workers.
