# Audit temporel v7.0 — stabilité 2027

## Règle de référence

La Pi est l'unique autorité de temps. Les contacts d'éclipse sont des instants UTC absolus.
L'iPad n'est jamais une source de temps ni de fuseau. La conversion locale est uniquement un affichage.

## Défauts identifiés dans la branche v6.6

1. **Horloge trigger mutable** — le mode réel relisait l'horloge système à chaque `now()`. Une correction NTP/GPS pendant une séquence pouvait donc faire sauter une fenêtre de phase, notamment le diamond ring.
2. **Deadlines caméra mutables** — les plugins Sony/Nikon recalculaient le temps restant avec l'horloge système UTC. Un saut de l'horloge pouvait modifier une décision de démarrage de bracket.
3. **Contacts sans date** — le moteur utilisait essentiellement `HH:MM:SS` et reconstruisait la date. Cela rendait les redémarrages/reprises et les passages de minuit ambigus.
4. **Horloge IHM dépendante du navigateur** — l'iPad avançait avec `Date.now()` et son fuseau local. Une reconnexion/reconfiguration de l'iPad pouvait donner un affichage différent de celui de la Pi.
5. **Affichage de `sync_time` dans le fuseau iPad** via `toLocaleTimeString()`.
6. **Timezone numérique non supportée** — `_timezone: 2.0` pouvait être traitée comme une chaîne et provoquer une erreur JavaScript.
7. **Synchronisation GPS autorisée pendant le trigger** — elle pouvait modifier brutalement l'horloge système en cours de séquence.

## Corrections v7.0

- `RuntimeClock` ancre l'UTC au lancement du trigger et avance avec `time.monotonic()`.
- Les deadlines caméra sont converties en deadlines `time.monotonic()` avant d'entrer dans les plugins.
- `/api/gps/sync` refuse une synchronisation si un trigger est actif.
- Les JSON nouveaux contiennent `_date_utc` et `contacts_utc` (ISO-8601 UTC avec date).
- Le trigger préfère `contacts_utc`; les anciens JSON restent compatibles.
- L'API temps expose `epoch_ms` UTC de la Pi.
- L'IHM s'ancre sur `epoch_ms` et avance avec `performance.now()`.
- Le fuseau iPad n'est plus utilisé comme fallback métier.
- `sync_time` est rendu avec le fuseau configuré par le backend, pas par l'iPad.

## Invariant 2027

Une fois le trigger réel lancé, sa chronologie ne dépend plus d'aucune modification de l'horloge murale Linux ou de l'iPad. Les phases et les barrières C2/C3 utilisent le même axe monotonic.
