# FEAT-111 — Contrat développeur de `GenericWorker`

## Objet et périmètre

`GenericWorker` exécute des callables de façon séquentielle sur un thread daemon
persistant. Une instance représente la file de travail d'un seul couple logique
RIG/type de device ; plusieurs instances permettent aux RIG ou aux types de
device de progresser indépendamment.

La classe fournit uniquement un mécanisme générique d'exécution et d'arrêt. Le
code appelant reste responsable du cycle de vie du service, du contenu des jobs
et de l'interprétation de leurs résultats.

## API

### Initialisation

`GenericWorker(rig_id, device_kind, log_fn=print, shutdown_policy="drain",
device_close=None, max_queue_size=None)` conserve l'identité utilisée pour le
nom du thread et les erreurs. `shutdown_policy` accepte seulement `drain` et
`cancel_pending`. `max_queue_size` doit être positif lorsqu'il est fourni ; en
son absence, la file n'est pas bornée. `device_close`, s'il est fourni, est
appelé au plus une fois à la fin du worker, ou lors de l'arrêt d'une instance
jamais démarrée.

### Démarrage et soumission

- `start()` crée le thread au premier appel. Un nouvel appel pendant qu'il est
  actif ne fait rien ; une instance arrêtée ne peut pas être redémarrée.
- `submit(callable, *args, **kwargs)` place un job dans la file et renvoie un
  `concurrent.futures.Future`. La valeur de retour ou l'exception du callable
  est publiée par ce future. Une file bornée pleine lève `queue.Full`.
- Un job peut être soumis avant `start()`. Après le début de l'arrêt, toute
  soumission lève `RuntimeError`.
- `running` indique si le thread existe et est encore vivant.

### Arrêt

`stop(timeout=None)` interdit immédiatement les nouvelles soumissions, puis
attend le thread au maximum pendant `timeout` :

- avec `drain`, le job en cours et tous les jobs déjà en file sont exécutés
  avant la terminaison ;
- avec `cancel_pending`, le job en cours se termine, mais les futures des jobs
  encore en file sont annulés ;
- si le worker n'a jamais été démarré, tous les jobs en attente sont annulés,
  quelle que soit la politique.

L'appel est répétable. Un timeout n'interrompt ni le callable en cours ni le
thread : `stop()` peut donc rendre la main alors que `running` est encore vrai.

### Dernière erreur

`last_error` renvoie `None` avant toute erreur de job, puis une copie du dernier
enregistrement `{rig_id, device_kind, message, when}`. `when` est un timestamp
ISO 8601 en UTC. Une exception de job est également placée dans son future et
journalisée ; elle ne tue pas le worker. Une erreur de `log_fn` est ignorée.
Les erreurs éventuelles de `device_close` sont journalisées mais ne remplacent
pas `last_error`.

## Invariants de threading multi-RIG

- Une instance possède au plus un thread et exécute au plus un job à la fois,
  dans l'ordre de sa file.
- Deux instances ne partagent ni file, ni thread, ni état d'erreur. Un job long
  sur un RIG ne bloque donc pas la file d'un autre worker.
- `submit()` n'exécute jamais le callable sur le thread appelant ; le future est
  le seul contrat de résultat asynchrone.
- Le worker ne garantit pas que les callables, les devices ou les ressources
  partagées sont thread-safe. Cette synchronisation appartient à l'appelant.
- `device_close` est une finalisation, pas une annulation forcée : elle intervient
  une fois la boucle terminée (ou directement lors de l'arrêt sans démarrage).

## Hors périmètre

- Création, détection, connexion ou configuration d'un device matériel.
- Affectation des workers aux RIG et orchestration de leur cycle de vie global.
- Priorités, parallélisme au sein d'une file, retry, délai d'exécution ou
  interruption forcée d'un job.
- Persistance, télémétrie, API utilisateur ou interface graphique.
- Modification du timing, du séquencement des phases, du watchdog, de l'audio,
  du GPS ou du comportement caméra, monture et focuser.
