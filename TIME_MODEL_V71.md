# Modèle temporel v7.1

## Circonstances

Le fichier JSON stocke séparément :

- `_date`: date UTC réelle de l'éclipse (`YYYY-MM-DD`),
- `_circumstances_location`: latitude, longitude, altitude et commentaire de validité,
- `C1`, `C2`, `TMAX`, `C3`, `C4`, `TSTART`, `TEND`: heures UTC indépendantes au format `HH:MM:SS.sss`.

La date n'est jamais fusionnée de manière permanente dans les heures de contact. `_date_utc` reste seulement un alias de compatibilité v7.0.

## Mode réel

Au lancement, `backend.timeline` construit une timeline UTC à partir de `_date` + heures. Le moteur l'ancre ensuite sur `time.monotonic()` via `RuntimeClock`. Les corrections ultérieures de l'horloge Linux ne déplacent pas les phases.

## Dry-run ×1

Le dry-run utilise le même `eclipse_trigger.py`, le même `CameraService`, le même plugin caméra, les mêmes phases, intervalles, sons, deadlines et protections. Il n'accélère rien.

La seule transformation est :

`TSTART réel -> maintenant + delay_s`

Tous les autres événements sont translatés du même delta. Tous les écarts, y compris les fractions de seconde, sont conservés exactement. La position GPS actuelle n'est pas utilisée pour recalculer les circonstances ; elle peut donc être Paris alors que le JSON décrit un site en Égypte.

## Simulation

La simulation accélérée reste un mode de développement séparé (`--simulate --speed N`) et ne doit pas être confondue avec le dry-run de qualification.

## Précision

Le calculateur Jubier et le backend conservent les millisecondes. Aucun `strftime("%H:%M:%S")` ne doit être utilisé comme représentation intermédiaire de scheduling ; il est autorisé uniquement pour l'affichage/logging.
