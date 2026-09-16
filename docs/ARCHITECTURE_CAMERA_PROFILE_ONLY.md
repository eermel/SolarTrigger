# Contrat d'architecture caméra

## Production
Un seul chemin : modèle physique -> profil caractérisé unique -> ProfilePlugin.
Aucun plugin Python constructeur ne peut contourner la caractérisation.

## Références
Sony/Nikon historiques restent des oracles DEV : fonctionnalité, optimisation,
benchmark et régression. Ils sont chargeables explicitement, jamais par le
loader de production.

## Sélection pendant la caractérisation
1. découvrir tous les chemins/recettes sûrs ;
2. prouver le résultat et le readback ;
3. répéter ;
4. rejeter toute méthode instable ou incomplète ;
5. comparer le pire temps, puis la médiane ;
6. écrire la recette sélectionnée dans le profil modèle ;
7. conserver les essais détaillés dans les measurements, pas dans le profil
   runtime compact.
