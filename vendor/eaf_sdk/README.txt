SDK ZWO EAF — à déposer ici
============================

Le focuseur ZWO EAF nécessite le SDK officiel ZWO (bibliothèque propriétaire),
qui ne peut pas être redistribué dans ce package. Déposez-le ici avant de lancer
l'installation si vous utilisez un focuseur ZWO EAF.

Comment faire :
  1. Téléchargez "EAF SDK for Linux & Mac" depuis :
       https://www.zwoastro.com/downloads/developers
  2. Décompressez l'archive (EAF_Linux_macOS_SDK_Vx.x.x.tar.bz2).
  3. Copiez dans ce dossier :
       - le dossier  eaf/lib/armv8/   (bibliothèque ARM 64 bits pour Raspberry Pi)
       - le fichier  eaf/lib/eaf.rules (règle udev)
     OU, plus simple, copiez tout le dossier "eaf/" décompressé ici.

Structure attendue par l'installation (au moins l'un des deux) :
   vendor/eaf_sdk/eaf/lib/armv8/libEAFFocuser.so.*
   vendor/eaf_sdk/eaf/lib/eaf.rules
  (ou)
   vendor/eaf_sdk/armv8/libEAFFocuser.so.*
   vendor/eaf_sdk/eaf.rules

Si le SDK est absent, l'installation continue normalement mais le focuseur ZWO
ne sera pas disponible (les autres fonctions du système ne sont pas affectées).
