# FEAT-126 — Couverture multi-monture et multi-focuser

FEAT-126 vérifie que les montures et focusers de plusieurs RIG peuvent progresser
concurremment. Le runtime maintient un worker persistant par device configuré :
chaque `MountWorker` ou `FocuserWorker` possède son propre `GenericWorker`, donc
sa propre file séquentielle et son propre thread. Les opérations d'un même device
restent sérialisées, sans ordre global entre les workers.

Les routes `/api/rigs/<rig_id>/mount/...` et
`/api/rigs/<rig_id>/focuser/...` sélectionnent exclusivement le worker du RIG
demandé. Une commande, une erreur ou l'arrêt d'un worker ne doit pas affecter les
workers des autres RIG. De même, la réconciliation ajoute ou retire uniquement
les workers concernés par la configuration ; désactiver un RIG laisse actifs les
workers des autres RIG.

## Tests ajoutés

- [`tests/test_multi_device_workers_runtime.py`](../tests/test_multi_device_workers_runtime.py)
  couvre la création simultanée de cinq workers caméra, monture et focuser.
- [`tests/tests_rig_mount_routes.py`](../tests/tests_rig_mount_routes.py) couvre
  le routage monture par `rig_id` et l'isolation d'un arrêt entre deux RIG.
- [`tests/tests_rig_focuser_routes.py`](../tests/tests_rig_focuser_routes.py)
  couvre l'isolation de `stop` et `jog/stop` entre deux RIG.
- [`tests/test_worker_runtime_disable_rig.py`](../tests/test_worker_runtime_disable_rig.py)
  couvre l'arrêt ciblé des workers monture et focuser lors de la désactivation
  d'un RIG.
