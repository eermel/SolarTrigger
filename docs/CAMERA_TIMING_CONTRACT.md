# Caractérisation caméra — contrat de timing v3

Le contrat v3 remplace le modèle basé sur une pose de référence et sur
`capture_setup`. Il ne cherche plus à extrapoler un temps de capture complet à
partir d'une exposition de référence.

## Politique de sécurité

Pour chaque grandeur caractérisée, la valeur opérationnelle est calculée dans
cet ordre exact :

    budget = arrondi_sup_50(maximum_observé × 1,10 + 50 ms)

Exemple :

    maximum observé = 804 ms
    804 × 1,10      = 884,4 ms
    + 50 ms         = 934,4 ms
    arrondi sup. 50 = 950 ms

La politique est écrite explicitement dans `safety_policy` du JSON final.

## Les quatre budgets du contrat

Le JSON final contient uniquement les valeurs opérationnelles sécurisées :

- `set_overhead_ms` : budget commun à toute commande SET utilisée par le plan.
  La caractérisation chronomètre séparément ISO, vitesse et mode de capture
  lorsqu'ils sont utilisés, puis retient le maximum de toutes les observations.
- `single_overhead_ms` : overhead fixe d'une photo simple, hors temps de pose.
- `bracket_overhead_ms` : overhead fixe d'un bracket, hors temps de pose.
- `bracket_inter_image_ms` : overhead ajouté entre deux images consécutives
  d'un bracket.

Les durées utilisées par le séquenceur sont donc :

    PHOTO simple =
        exposition_ms
        + single_overhead_ms

    PHOTO bracket N =
        somme(expositions_ms)
        + bracket_overhead_ms
        + (N - 1) × bracket_inter_image_ms

Aucune pose de référence n'est utilisée à l'exécution.

## Mesure du bracket

Pour chaque taille de bracket validée, la caractérisation soustrait la somme
des temps de pose au temps total observé. Elle retient le maximum d'overhead
observé pour chaque taille.

Quand plusieurs tailles sont disponibles, l'inter-image brut est le plus grand
accroissement d'overhead par image entre deux tailles mesurées. L'overhead fixe
brut est ensuite le plus grand résidu nécessaire pour couvrir toutes les
tailles mesurées.

Quand une seule taille est disponible, l'inter-image n'est pas identifiable :
tout l'overhead mesuré est conservé dans la partie fixe. La politique de sécurité
est ensuite appliquée aux deux composantes.

Le plan n'utilise jamais une taille de bracket qui n'a pas été validée.

## SET et reprise

Le contrat v3 ne génère plus de macro `SET capture_setup`. Chaque groupe de
photos est autonome et contient les SET physiques nécessaires :

    SET ISO
    SET mode simple        # si le boîtier expose ce réglage
    SET vitesse
    SET mode bracket       # uniquement pour un bracket
    PHOTO

Chaque SET réserve `set_overhead_ms`.

Cette redondance est volontaire : après une erreur USB, un groupe futur complet
peut repartir sans reconstruire l'historique des anciens SET. Le Trigger continue
sur ses horaires absolus et ne rejoue jamais une photo passée.

Le format `.plan` conserve l'enveloppe de garde existante
`timing_contract_version=2` pour le transport IPC et le rejet des commandes
périmées. Cette valeur est un détail du protocole d'exécution ; les durées qu'elle
transporte proviennent du modèle caméra v3.

## Pause de 2 secondes

Les deux secondes sans événement USB sont exclusivement une séparation entre
essais de caractérisation. Elles :

- ne font pas partie des mesures ;
- ne sont pas ajoutées aux budgets ;
- ne sont pas écrites dans le `.plan` ;
- ne sont jamais exécutées par le Trigger.

La confirmation des fichiers reste, elle, incluse dans la mesure de PHOTO.

## JSON final et données de debug

Le fichier final `configs/camera_timing/<profil>.json` ne contient pas
l'historique des essais. Il contient seulement l'identité du boîtier et le
`timing_contract` v3 avec les valeurs sécurisées.

Les échantillons bruts, médianes, essais rejetés et pauses de test restent dans :

    configs/camera_characterization/measurements/<job_id>.json

Ils servent au diagnostic mais ne sont jamais lus par le Trigger.

Le profil final `configs/camera_profiles/<profil>.json` conserve seulement les
commandes validées, la stratégie retenue, les modes de bracket utilisables et le
contrat v3. Les benchmarks et données brutes de caractérisation ne sont pas
publiés dans le profil runtime.

## Compatibilité

Les profils historiques et les contrats v2 restent lisibles. Ils ne sont ni
convertis ni réécrits automatiquement. Une nouvelle caractérisation est nécessaire
pour obtenir un contrat v3.

Le nouveau modèle ne mesure toujours pas le début physique de l'exposition.
La latence physique déclenchement → ouverture de l'obturateur reste donc
explicitement non corrigée.
