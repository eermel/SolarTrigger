# Caractérisation et budgets d'exécution — contrat 2

Cette évolution relie les mesures au protocole réellement exécuté par les profils
JSON. Les plugins historiques restent compatibles ; leurs valeurs ne sont pas
réinterprétées ni modifiées. Recaractériser puis régénérer les `.plan` est nécessaire
pour utiliser ce contrat.

## Critère d'acceptation

Un profil est publié seulement après la découverte des commandes, cinq mesures
par méthode validée et une qualification automatique sans pause de test. Celle-ci
utilise le même exécuteur que le Trigger, les budgets majorés et la confirmation des
fichiers. Chaque bloc retenu doit terminer dans son budget. Sinon aucun profil
n'est installé : il faut examiner la cause, pas utiliser une mesure trop optimiste.

La qualification comprend cinq parcours de neuf vitesses pour les photos simples,
et cinq bracketing par taille retenue. Les ISO sont fixés à 100. Les commandes
capture et bulb sont comparées par taille lorsqu'elles sont disponibles ; bulb
n'est jamais essayé pour une photo simple. Les confirmations opérateur restent
limitées à la découverte. Une commande avec un nombre de fichiers incorrect est
rejetée après la confirmation opérateur, sans chronométrage supplémentaire.

## Ce qui est chronométré

- Écriture ISO avec confirmation de la valeur effective.
- Écriture de vitesse avec confirmation de la valeur effective.
- Bloc de préparation : mode photo simple si disponible, vitesse centrale,
  puis configuration du bracketing si demandé. Ce bloc englobe les transitions ;
  aucun chronométrage séparé du changement de mode n'est effectué.
- Bloc PHOTO : appel, attente des fichiers et relâchement éventuel.

La batterie, le passage initial en manuel, RAW, destination carte, la désactivation
du retardateur et du time-lapse ne sont pas chronométrés. Ces deux derniers
réglages sont désactivés si un widget connu et une valeur explicite sont exposés ;
un réglage inconnu reste signalé, jamais deviné. L'ouverture reste un réglage de
préparation ; aucun changement d'ouverture n'est introduit entre les photos.

## Marge explicite

Pour les réservations opérationnelles :

    budget = arrondi_sup_50ms(maximum_observé × 1,10 + 50 ms)

Ainsi, un maximum de 300 ms donne 400 ms ; un maximum de 804 ms donne 950 ms.
Cette politique volontairement conservatrice est enregistrée dans les fichiers.
C'est une marge d'ingénierie, pas une borne statistique garantie par cinq essais.
Les échantillons restent accessibles dans timing_trials, set_trials et setup_trials.
Les médianes brutes restent diagnostiques ; elles ne déterminent plus les budgets.
Les coûts de relâchement sont déjà inclus dans le bloc PHOTO et ne sont pas ajoutés
une seconde fois.

La pose de référence est déjà comprise dans le bloc mesuré. Pour une pose plus
longue, seul son excédent est ajouté avec 10 % de marge et arrondi supérieur. Une
pose plus courte ne réduit pas le budget mesuré. Cette extrapolation doit être
validée par un dry-run du plan définitif, notamment pour les poses longues.

Le retour USB, la disponibilité d'une commande suivante, l'apparition du fichier
et le début physique de l'exposition ne sont pas synonymes. Cette version conserve
la confirmation du fichier comme critère conservateur ; elle ne prétend pas avoir
prouvé qu'on peut reprendre dès le retour de trigger_capture. Elle ne remplace donc
pas automatiquement les quelque 900 ms du D850 par les 285 ms historiques.
La latence physique reste explicitement non mesurée : zéro désactive la correction,
ce n'est pas une mesure de latence nulle. Sa mesure demande une référence matérielle.

## Plan et reprise

Le `.plan` reste constitué de SET et PHOTO. SET capture_setup est une préparation
complète avec son propre budget. Le découpage optimisé compare les blocs avec leurs
coûts de préparation et les durées des expositions demandées. Chaque groupe porte
son ISO et sa préparation pour permettre une reprise sans rejouer les commandes
passées. Ce choix réserve aussi le coût ISO pour chaque groupe ; il privilégie une
reprise déterministe à l'économie de cette transaction.

Pour les commandes du contrat 2 :

- Le délai IPC est adapté au budget de la commande ; ce délai de transport n'est
  pas ajouté à la réservation du plan.
- Un worker occupé refuse la commande au lieu de l'empiler. Une commande non
  commencée sous 100 ms après son émission IPC expire. Ce délai d'admission local
  devra être vérifié sous charge sur la Pi.
- Une erreur ou un dépassement n'arrête pas le RIG. Les horaires absolus continuent,
  les commandes passées sont abandonnées, aucune photo n'est rejouée.
- Une PHOTO dont un SET requis a été manqué est également abandonnée. Une préparation
  complète future permet la reprise. Aucun SET historique n'est restauré lors de
  la reprise d'un nouveau plan.

Un appel libgphoto2 déjà bloqué ne peut pas être interrompu sûrement par cette
couche Python. Tant que le worker ne revient pas, les nouvelles commandes sont
refusées ; les autres RIG continuent. La caractérisation vise à éviter ce cas en
fonctionnement normal, la reprise est seulement une protection contre les incidents.

## Limites de qualification

Aucun appareil physique n'est accessible à l'environnement de développement.
Les simulations valident le logiciel, pas la cadence réelle du D850 ou du Sony.
La qualification locale couvre les poses et répétitions enregistrées, un RIG à la
fois. Elle ne garantit pas toutes les cartes mémoire, la température, les poses
longues ou la contention USB/CPU de plusieurs RIG. Le dry-run final doit reproduire
les réglages, les cartes et tous les RIG de l'éclipse.

Les deux secondes de silence sont exclusivement une pause entre essais de
caractérisation. Elles sont exclues des budgets, de la qualification enchaînée et
du Trigger. Les configurations existantes et l'historique ne sont jamais supprimés
par l'installation de cette évolution.
