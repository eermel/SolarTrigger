# FEAT-127 — Adressage multi-device INDI

Une monture logique INDI est adressée, au sein d'un `indiserver`, par son
**nom de device INDI**. Ce nom est la clé d'isolation des lectures et des
écritures : les propriétés demandées ou modifiées sont préfixées par ce nom et
les réponses sont regroupées puis sélectionnées avec la même clé. Deux montures
exposées sous le même nom par un même serveur ne sont donc pas distinguables.

Plusieurs montures logiques peuvent partager les mêmes `host` et `port`, et par
conséquent la même instance d'`indiserver`, à condition d'utiliser des noms de
devices distincts. Ce partage concerne uniquement le transport INDI. Il ne
mutualise ni les files de commandes métier ni leur exécution.

Chaque RIG conserve son `MountWorker`, avec sa propre file séquentielle et son
propre thread. Une opération longue ou une erreur sur une monture ne doit pas
ordonner, consommer ou bloquer les commandes destinées aux autres RIG ; seules
les opérations d'une même monture sont sérialisées dans son worker.

La configuration mono-monture reste inchangée : un seul RIG, un seul worker et
un seul nom de device continuent d'utiliser `host` et `port` comme auparavant.
Le support multi-device n'impose ni nouveau schéma de configuration ni
modification d'interface utilisateur.
