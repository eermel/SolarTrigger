# DEV-003 — API de photo de test par RIG

## Route

`POST /api/rigs/<rig_id>/camera/test_photo` déclenche une unique photo de
diagnostic sur la caméra du RIG activé désigné par `rig_id`.

Le corps JSON doit contenir le champ `speed`, sous forme d'une vitesse
d'exposition textuelle non vide :

```json
{
  "speed": "1/125"
}
```

Exemple :

```http
POST /api/rigs/1/camera/test_photo
Content-Type: application/json

{"speed":"1/125"}
```

## Réponse réussie

La route répond avec le statut HTTP `200`. `duration_s` mesure, en secondes,
le temps écoulé pendant l'appel de capture caméra. `started_at` est un
horodatage ISO 8601 avec fuseau. Les champs `frames`, `planned` et `detail`
sont inclus lorsqu'ils sont fournis par le résultat de capture.

```json
{
  "status": "ok",
  "rig_id": 1,
  "speed": "1/125",
  "started_at": "2026-08-28T10:15:30.123456+00:00",
  "duration_s": 0.482731,
  "frames": 1,
  "planned": 1,
  "detail": "single"
}
```

## Erreurs

Les erreurs sont des objets JSON contenant au minimum `error`; les erreurs
caméra ci-dessous exposent également un `code` stable.

| HTTP | `code` | Cas |
| --- | --- | --- |
| `400` | `INVALID_TEST_PHOTO_SPEED` | Corps absent ou invalide, `speed` absent, vide, non textuel ou vitesse non reconnue. |
| `409` | `DEVICE_NOT_CONFIGURED` | Aucune caméra n'est configurée pour ce RIG. |
| `409` | `CAMERA_BUSY` | Le worker caméra exécute déjà une opération incompatible avec ce diagnostic. |
| `404` | `CAMERA_UNAVAILABLE` | La caméra ne peut pas effectuer la capture, notamment si elle est déconnectée ou en erreur. |

Exemple de caméra non configurée :

```json
{
  "error": "camera is not configured for rig 1",
  "code": "DEVICE_NOT_CONFIGURED",
  "rig_id": 1,
  "device_type": "camera"
}
```

Un identifiant de RIG invalide produit une erreur `400` sans code caméra. Un
RIG existant mais désactivé produit une erreur `409`, également sans code
caméra.

## Neutralité et périmètre matériel

La route ne modifie ni ne persiste la configuration du RIG ou les réglages
enregistrés de la caméra. La valeur `speed` sert uniquement à la photo de test
demandée. Cet appel ne commande ni la monture ni l'EAF et ne déclenche aucune
phase de la séquence d'éclipse.
