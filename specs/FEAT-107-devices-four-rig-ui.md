# FEAT-107 — Interface Devices à quatre RIG

## Objet et périmètre

Cette spécification décrit le contrat développeur de l'écran **Devices** à
quatre colonnes, la représentation des RIG publiée par le backend et le cycle
de vie du `RigManager`. Elle couvre l'état livré par `backend/rig_runtime.py`,
`flask_app/app.py` et `flask_app/templates/index.html`.

Le contrat présenté à l'interface est un résumé de configuration. Il ne décrit
pas l'état d'une connexion matérielle et ne déclenche aucune initialisation de
device.

## Normalisation fixe des RIG

`normalize_rigs_for_ui(rm)` produit toujours une liste de quatre objets, dans
l'ordre des slots 1 à 4. Chaque objet expose uniquement :

| Champ | Type | Valeur |
|---|---|---|
| `rig_id` | entier | Numéro fixe du slot, de 1 à 4. |
| `name` | chaîne | Nom du RIG configuré, ou `RIG n` si le slot est absent. |
| `enabled` | booléen | État configuré, ou `false` si le slot est absent. |

Un slot non configuré n'est donc pas omis : il est synthétisé, nommé avec la
valeur par défaut et désactivé. Les devices, les références aux services et les
warnings d'identité du `RigManager` ne font pas partie de cette réponse UI.

## Contrat de statut backend

La réponse JSON de `GET /api/status` contient toujours une clé `rigs` conforme
à la normalisation ci-dessus.

Chaque événement Socket.IO `status_update` construit par
`_status_update_payload(base)` contient lui aussi `rigs`, en plus des données
propres à l'événement et de `time`. La clé ajoutée par le helper remplace une
éventuelle clé homonyme de `base`; les producteurs doivent donc considérer ce
helper comme la source canonique du résumé des RIG.

Dans les deux chemins, le backend appelle le `RigManager` canonique puis le
normaliseur. Si le chargement, la migration, la validation ou la normalisation
échoue, l'erreur est journalisée et la réponse reste exploitable : elle contient
les quatre objets `{rig_id: n, name: "RIG n", enabled: false}`. La présence de
`rigs` n'est pas conditionnée par le succès du chargement ni par la présence de
RIG configurés.

## Cycle de vie du `RigManager`

`backend/rig_runtime.py` est le point d'accès canonique :

1. `get_rig_manager()` cherche `configs/rig/default.json` et le charge lorsqu'il
   existe ;
2. en son absence, il migre l'état historique depuis
   `flask_app/state.json` et `configs` uniquement en mémoire ; cette lecture ne
   crée pas `configs/rig/default.json` ;
3. il construit le manager avec `RigManager.from_config()` ;
4. il conserve l'instance en singleton, avec une initialisation protégée par un
   verrou pour les appels concurrents.

La construction valide la configuration et crée des objets `Rig`, mais laisse
`camera_service`, `mount_service` et `focuser_service` à `None`. Elle ne sonde,
ne connecte et ne démarre aucun matériel. L'attachement éventuel de services
appartient à une phase ultérieure du cycle de vie de l'application.

`reset_rig_manager_for_tests()` invalide le singleton sous le même verrou. Il
est réservé à l'isolation des tests : l'appel suivant recharge la configuration
et crée une nouvelle instance.

## Rendu frontend et replis

La section Devices contient un bloc GPS global, suivi d'une rangée statique de
quatre colonnes `RIG 1` à `RIG 4`. Chaque colonne possède son propre switch et
son propre corps de contrôles caméra, monture et focuser. Le GPS n'est pas
répété dans les colonnes.

Le frontend définit `DEFAULT_RIGS`, quatre slots désactivés. Au chargement et à
la reconnexion, la réponse de `/api/status` alimente `updateRigs`. Les événements
`status_update` font de même. Si `payload.rigs` est absent ou faux, le frontend
utilise `DEFAULT_RIGS`; `updateRigs` utilise également les valeurs par défaut
pour tout identifiant manquant et ignore, pour le rendu des slots, les
identifiants autres que 1 à 4.

Pour chaque slot, seul `enabled === true` active la colonne. Le switch est
synchronisé, la classe CSS `enabled` est appliquée et les champs, sélecteurs et
boutons du corps sont activés ou désactivés en conséquence. Un changement de
switch est traité par délégation et reste limité à sa propre colonne.

À ce stade, ce changement local ne persiste pas la configuration et ne demande
pas au backend d'activer un RIG. De même, les sélecteurs affichés dans les
colonnes sont construits à partir du snapshot historique global `devices`; le
résumé `rigs` ne transporte pas de configuration device par RIG.

## Hors périmètre

- Manuel utilisateur ou procédure d'exploitation.
- Persistance des switches et édition de la configuration des RIG.
- API de commande, détection ou état matériel propre à chaque RIG.
- Attachement ou cycle de vie des services caméra, monture et focuser.
- Modification du GPS global, de l'audio, du watchdog, du timing, des phases ou
  du séquencement de déclenchement.
- Ajout de polling frontend ; l'actualisation repose sur `/api/status` et les
  événements existants.

## Matrice minimale de tests

| Couche | Cas minimal | Résultat attendu |
|---|---|---|
| Backend — normalisation | Manager partiel, avec slots configurés et absents | Quatre objets ordonnés ; noms et états configurés sont conservés, slots absents désactivés. |
| Backend — migration | Configuration v2 absente, état historique présent | Migration en mémoire, aucun fichier v2 créé, quatre slots normalisés. |
| Backend — cycle de vie | Deux appels, puis reset et nouvel appel | Même instance avant reset, nouvelle instance après reset ; une seule construction par cycle. |
| Backend — neutralité matérielle | RIG désactivés puis activés valides | Construction réussie et trois références de service à `None`. |
| Backend — HTTP | `GET /api/status` avec manager partiel | Clé `rigs` présente et exactement quatre slots normalisés. |
| Backend — Socket.IO | Connexion et émission métier via `_status_update_payload` | Chaque `status_update` inclut les quatre slots et `time`. |
| Backend — erreur | Échec du chargement ou de la normalisation | HTTP et Socket.IO publient quatre slots par défaut désactivés. |
| Frontend — structure | Inspection de la section Devices | Un GPS global, puis exactement quatre colonnes numérotées 1 à 4. |
| Frontend — alimentation | Statut HTTP et `status_update`, avec `rigs` présent ou absent | `updateRigs` reçoit le payload ou `DEFAULT_RIGS`. |
| Frontend — isolation | Changement du switch d'une colonne | Classe et contrôles de cette colonne seulement sont mis à jour. |
| Frontend — cadence | Inspection de la logique Devices | Aucun nouveau `setInterval` ou `setTimeout`. |
