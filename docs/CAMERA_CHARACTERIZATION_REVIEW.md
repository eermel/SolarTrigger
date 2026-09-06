# Revue de caractérisation : commandes et usure

## Règles appliquées

La photo simple et le bracketing ont des validations indépendantes. L'identité
exacte d'une commande inclut méthode, chemin du widget, valeur et relâchement.
Ainsi camera.capture() n'est pas le widget /main/actions/capture.
Les tailles de bracketing sont explorées dans l'ordre croissant. Une commande
rejetée est exclue des tailles suivantes pour cette caractérisation, sans modifier
son éventuelle validation en photo simple. Un refus de l'opérateur dès 3 vues
entraîne donc zéro répétition de chronométrage et zéro essai à 5, 7 ou 9 vues.
L'annulation est propagée, jamais transformée en rejet de commande.

Une seule commande est exportée pour toutes les tailles disponibles validées.
La sélection exige la réussite de chaque taille découverte ; elle compare la somme
des maxima de capture sur les mêmes tailles, hors temps de préparation.
Aucune combinaison de méthodes différentes n'est exportée. Si aucune commande
commune ne subsiste, le profil reste séquentiel avec un avertissement.
Les JSON antérieurs ne sont ni convertis ni supprimés par ce correctif.

## Économie de déclenchements

L'ancienne comparaison faisait reprendre 45 photos simples puis, si le bracketing
était disponible, 45 autres vues. Elle est supprimée. La comparaison finale
utilise les budgets et est explicitement identifiée comme un calcul.
La découverte conserve un essai opérateur par commande et taille ; une commande
validée est mesurée sur cinq essais automatiques, puis la méthode retenue est
qualifiée sur cinq répétitions. Seul le bloc concerné recommence si son budget
ou sa marge sont insuffisants. Les reprises n'ont pas de limite : l'opérateur
peut annuler. Le nombre total de photos ne peut donc pas être garanti à l'avance.

Le test logiciel avec deux méthodes et les tailles 3/5 compte explicitement
193 vues simulées au lieu de 283, sans les 90 vues de comparaison redondantes.
Ce nombre correspond à cette simulation, pas au nombre promis pour chaque Sony.

## Cohérence et marges

Le parcours de comparaison non vérifié a été retiré. Les préparations des
bracketing et leur retour en mode simple utilisent les écritures vérifiées.
La qualification utilise toujours le même protocole que l'exécuteur de profils.
Les deux secondes entre essais restent exclues des budgets et de la qualification.
Le délai d'observation de qualification reste indépendant du budget évalué.

Une série ne passe plus seulement parce que ses durées sont sous le budget :
la marge maximum × 1,10 + 50 ms, arrondie au-dessus à 50 ms, doit aussi être
présente sur ses maxima finaux. Les maxima validés sont enregistrés dans
validated_maxima_ms. Une marge insuffisante entraîne un recalcul suivi d'une
nouvelle série complète à budgets figés. La correction tient compte de l'excédent
d'exposition pour ne pas doubler sa contribution dans le modèle de durée.

## Conservation des résultats

Les mesures sont sauvegardées atomiquement après les séries dans
configs/camera_characterization/measurements/<job_id>.json et à la fin du travail,
y compris en cas d'échec ou d'annulation. Ces fichiers ne sont pas des plugins
et ne sont pas découverts par le registre des appareils.
Ils contiennent les données et l'issue du travail, pas les logs de debug.
Il n'y a pas encore de reprise automatique de caractérisation depuis ces fichiers :
leur réemploi nécessite de vérifier le protocole et les conditions matérielles.

## Limites maintenues

La marge 10 % + 50 ms reste une politique d'ingénierie provisoire, pas une garantie
statistique. La latence physique reste non mesurée. Les budgets sont liés au
protocole vérifié actuel, qui comporte encore des écritures ISO et de mode
redondantes. Ce correctif n'annonce pas de gain de cadence du Trigger : supprimer
ces transactions à l'exécution nécessite un protocole de reprise cohérent et une
validation dédiée. Aucun changement de cette nature n'est fait silencieusement.

Aucun essai matériel n'est déclenché par l'installation. Conserver la caractérisation
Sony réussie ; il n'est pas nécessaire de l'effacer pour vérifier ce correctif.

## Vérification

Tests simulés : refus opérateur à 3 vues, absence d'essais aux tailles suivantes,
indépendance simple/bracketing, ordre croissant malgré des choix désordonnés,
sélection commune indépendante du bruit de préparation, impossibilité de mélanger
les commandes, confirmation tardive ou absente, annulation, révisions répétées,
marge finale et sauvegarde hors du registre des profils.
La suite complète exécutée localement donne 1551 réussites et 3 tests ignorés ;
12 tests de sockets échouent avec PermissionError imposé par l'environnement.
La suite complète doit donc être exécutée sur la VM avant tout déploiement.
