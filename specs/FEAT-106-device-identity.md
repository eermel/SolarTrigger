# FEAT-106 — Structure `DeviceIdentity`

## Objet et périmètre

`DeviceIdentity` est l'objet de configuration associé à un équipement dans
`rigs[].devices.camera`, `rigs[].devices.mount` ou
`rigs[].devices.focuser`. Il rassemble le backend à utiliser, des informations
descriptives et les valeurs permettant d'identifier une instance physique.

Ce document décrit le schéma et les règles actuellement appliquées par
`backend/device_identity.py`. Il ne définit ni écran de gestion des devices, ni
nouveau protocole, ni changement de worker ou d'accès au matériel.

## Champs

| Champ | Rôle | Utilisation pour l'unicité |
|---|---|---|
| `backend` | Identifiant du backend/plugin chargé pour la catégorie. | Aucune. Il décrit le pilote, pas une instance physique. |
| `manufacturer` | Fabricant déclaré ou détecté de l'équipement. | Aucune. Plusieurs unités peuvent avoir le même fabricant. |
| `model` | Modèle déclaré ou détecté. | Aucune. Deux unités du même modèle restent distinctes. |
| `serial` | Numéro de série stable propre à l'unité, lorsqu'il est disponible. | Clé d'identité prioritaire. |
| `fallback_physical_path` | Chemin physique stable utilisé seulement en l'absence de `serial`. | Clé d'identité de repli, assortie d'un warning. |
| `alias` | Nom lisible choisi pour présenter ou repérer l'équipement. | Aucune. Un alias n'est pas une identité matérielle. |

La migration de configuration initialise ces six champs pour chaque device
actif, avec `null` pour les informations inconnues. Le validateur de forme de la
configuration v2 accepte toutefois des champs additionnels et n'exige pas que
les six clés soient toutes présentes. Une monture ou un focuser non configuré
peut être représenté par `null`; la caméra doit être un objet.

## Priorité de résolution de l'identité

Pour chaque device, l'identité stable est résolue dans cet ordre :

1. `serial`, s'il contient une valeur utilisable ;
2. `fallback_physical_path`, si aucun serial utilisable n'est présent ;
3. aucune identité, si les deux valeurs sont absentes ou vides.

Lorsqu'un `serial` est retenu, le fallback éventuel est ignoré : il ne participe
ni à la détection de collision ni à la génération d'un warning. `backend`,
`manufacturer`, `model` et `alias` ne sont jamais utilisés comme solutions de
repli implicites.

Une valeur de serial au format `usb:bus,device`, par exemple
`usb:001,006`, est interdite. Cette adresse dépend de l'énumération USB de la
session et n'est pas une identité persistante. Le contrôle est effectué avant
la recherche de collisions et rejette la configuration au lieu de basculer sur
`fallback_physical_path`.

## Unicité et collisions

L'unicité est contrôlée séparément dans chacune des catégories `camera`,
`mount` et `focuser`, sur l'ensemble des RIG configurés. La clé comparée est le
couple formé par le type d'identité retenu (`serial` ou `fallback`) et sa valeur.

En conséquence :

- deux devices de la même catégorie ayant le même serial sont refusés ;
- deux devices de la même catégorie ayant le même chemin de fallback retenu
  sont refusés ;
- une même valeur utilisée une fois comme serial et une fois comme fallback ne
  constitue pas la même clé ;
- une même clé dans deux catégories différentes n'entre pas en collision.

Une collision lève une erreur `duplicate device identity` et empêche la
construction du `RigManager`. Un device sans identité résolue ne fournit aucune
clé à comparer ; ce cas n'est donc pas, à lui seul, une collision.

## Warning lié au fallback

L'utilisation effective de `fallback_physical_path` est acceptée mais ajoute un
warning d'identité au `RigManager`. Le message désigne le RIG et la catégorie et
recommande de préférer un serial stable. Cette alerte rappelle qu'un chemin
physique est une solution de repli, potentiellement plus fragile qu'un numéro
de série propre au matériel.

L'absence simultanée de serial, d'alias et de fallback ne produit actuellement
aucun warning. L'alias n'influence pas ce comportement.

## Neutralité d'exécution

`DeviceIdentity` est un contrat de configuration et de validation. Les règles
ci-dessus ne modifient aucun protocole matériel, worker, service, séquencement
de phases ou mécanisme de timing. Elles ne sélectionnent ni ne connectent un
équipement : la validation est effectuée avant l'attachement ultérieur des
services matériels aux RIG.
