# FEAT-102 — Audit de l’identité matérielle multi-device

## Objet et périmètre

Ce document décrit l’identification actuellement observable dans le dépôt pour
les caméras, les montures INDI et les focuseurs ZWO EAF. Il distingue la
sélection d’un **type de plugin** de l’identification d’une **instance physique**.
Il s’agit d’un état des lieux : aucun stockage d’identité persistante, accès au
matériel ou changement de comportement n’est proposé ici.

## REQ-001 — Vocabulaire d’identité

- Le **modèle** (par exemple `Nikon D850`) et l’identifiant de plugin décrivent
  une famille de matériel. Ils ne distinguent pas deux exemplaires identiques.
- Une **identité persistante** est une valeur propre à une unité et assez stable
  pour retrouver cette même unité après débranchement, redémarrage ou changement
  de topologie USB. Un numéro de série matériel ou un identifiant stable fourni
  par un SDK peut remplir ce rôle, sous réserve des garanties du fournisseur.
- Une adresse gphoto2 telle que `usb:bus,device` est une **adresse runtime** : le
  bus et surtout le numéro de device sont attribués pendant l’énumération USB et
  peuvent changer. Elle permet d’adresser sans ambiguïté une caméra pendant une
  session donnée, mais ne constitue pas à elle seule une identité persistante.
- Le nom d’un device INDI est une identité logique dans l’espace de noms d’un
  serveur INDI. Sa persistance dépend de la configuration et du driver ; ce
  n’est pas, dans le code audité, un numéro de série matériel.

## REQ-002 — Caméra : identification actuelle

`plugins/camera/__init__.py:get_camera_model` cherche une chaîne de modèle, dans
cet ordre : abilities de l’objet caméra, champs `cameramodel`, `model` ou
`modelname` de sa configuration, puis `gp.Camera.autodetect()`. Les libellés PTP
génériques sont ignorés. Le fallback d’autodétection parcourt les couples
`(model, port)`, ignore explicitement le port et retourne le premier modèle
spécifique.

`load_plugin` applique ensuite `matches(model)` aux classes de plugins classées
par spécificité. Il sélectionne donc une implémentation compatible avec le
modèle, pas une caméra physique. `CameraService.connect` crée ou reçoit un seul
objet caméra, l’initialise, mémorise seulement son modèle et charge le plugin à
partir de ce même modèle.

Le code actuel ne lit ni n’utilise de **numéro de série caméra**, ni le **port
gphoto2** (notamment `usb:bus,device`), pour identifier ou sélectionner une
caméra individuelle. Il ne conserve pas non plus de correspondance persistante
entre une caméra et une configuration/RIG.

## REQ-003 — Monture INDI : identité et transport

`IndiSubprocessClient` porte quatre paramètres indépendants : `host`, `port`,
`device` et timeout. Toutes les lectures et écritures préfixent les propriétés
avec le `device name`; le parsing regroupe aussi les réponses par ce nom.
`ensure_device_present` vérifie précisément la présence de ce nom.

L’isolation est donc réalisée par **device name**. Plusieurs clients peuvent
partager le même `host` et le même `port`, donc un **indiserver commun**, tout en
ciblant des devices différents. Dans l’espace `(host, port)`, l’unicité attendue
par le code est celle du nom de device. Deux équipements exposés sous le même
nom sur le même serveur ne seraient pas distinguables par ce client.

`IndiMount` propage la configuration `device` au client et utilise également
`serial_port` pour configurer la propriété INDI `DEVICE_PORT` lors de la
connexion. Ce chemin série sert au transport/configuration de la monture ; le
code audité ne le transforme pas en identité persistante et ne lit pas de
numéro de série de monture.

## REQ-004 — ZWO EAF : identité SDK et limite actuelle

Le SDK EAF expose un identifiant numérique : `ZwoEaf.connect(index)` obtient
l’ID avec `EAFGetID`, ouvre l’unité par `EAFOpen(ID)`, puis appelle
`EAFGetProperty(ID, ...)`. La structure retournée contient aussi `ID`, `Name` et
`MaxStep`. Cet **ID SDK est utilisable comme identité stable au sein du SDK
EAF** et il est exposé dans les informations de connexion et le statut.

La sélection actuelle reste toutefois orientée mono-device : `connect` utilise
l’index 0 par défaut, `ZwoFocuser.connect` ne fournit aucun index/ID configuré,
et `probe` considère seulement qu’au moins un EAF peut être ouvert. Il n’existe
pas de binding persistant d’un ID EAF vers une configuration/RIG, ni de
sélection explicite d’un second EAF.

## REQ-005 — Détection backend et présentation Flask

`backend/devices.py` produit pour chaque catégorie un résultat de détection et
une suggestion de plugin. Pour la caméra, `detect_camera` ne transporte qu’un
modèle et `camera_plugin_for_model` suggère un plugin seulement si la résolution
est non ambiguë. Pour monture et focuser, `_probe_registry` agrège les plugins
dont `probe()` réussit et ne suggère un plugin que s’il n’en reste qu’un. Ces
résultats indiquent une compatibilité/type disponible, pas l’identité d’une
instance parmi plusieurs unités d’un même type.

`flask_app/app.py:_get_camera_model_info` réutilise `get_camera_model` pour
afficher marque et modèle, puis lit séparément la batterie. Il n’ajoute ni
serial, ni port gphoto2, ni identifiant d’instance. Les endpoints de statut et
de probe construisent par ailleurs un unique objet `gp.Camera()` sans lui
affecter de port particulier.

## REQ-006 — Capacités, limites et implications multi-device

| Catégorie | Clé actuellement exploitée | Capacité multi-device observée | Limite d’identité |
|---|---|---|---|
| Caméra | modèle / classe de plugin | aucune sélection explicite d’une instance ; autodétection réduite au premier modèle | deux exemplaires identiques sont confondus ; port et serial inutilisés |
| Monture INDI | `(host, port, device name)` | plusieurs noms de devices peuvent partager un indiserver | unicité seulement par nom dans le serveur ; pas de serial matériel |
| ZWO EAF | index d’énumération puis ID SDK | le SDK sait énumérer et fournir un ID | plugin branché par défaut sur l’index 0 ; aucun binding d’ID configuré |
| Backend/UI | modèle ou plugin suggéré | sait éviter une suggestion lorsque plusieurs types matchent | ne retourne pas une liste d’instances adressables ni une identité persistante |

En conséquence, une évolution multi-RIG devra séparer au minimum : identité
persistante, adresse runtime/transport, modèle et plugin. Le présent audit ne
prescrit ni leur schéma de stockage ni leur stratégie de migration.

## Deux Nikon D850 identiques

Avec deux D850 connectés, `gp.Camera.autodetect()` peut retourner deux entrées
ayant le même modèle mais des ports runtime distincts, par exemple
`usb:001,006` et `usb:001,009`. Le code actuel jette les ports, retient le
premier modèle spécifique et sélectionne dans les deux cas le même plugin Nikon
DSLR. La création d’un `gp.Camera()` sans configuration explicite de port ne
matérialise aucune association déterministe entre un boîtier et un RIG.

Le système peut donc reconnaître la famille « Nikon D850 », mais ne peut pas :

- nommer séparément D850 A et D850 B ;
- garantir qu’une configuration est réappliquée au même boîtier après une
  ré-énumération USB ;
- router avec certitude une capture vers l’un des deux boîtiers ;
- détecter qu’ils ont été intervertis.

Le port `usb:bus,device` pourrait servir à l’adressage pendant la session, mais
pas de clé persistante puisqu’il peut changer. Une identité durable nécessiterait
une donnée propre à chaque boîtier (typiquement un serial) et un binding séparé
vers son adresse runtime. Ces mécanismes sont absents du code actuel et leur
implémentation est hors périmètre de cet audit.

## Fichiers audités

- `plugins/camera/__init__.py` et `services/camera_service.py`
- `plugins/mount/indi_client.py` et `plugins/mount/indi_plugin.py`
- `plugins/focuser/zwo_eaf.py` et `plugins/focuser/zwo_plugin.py`
- `backend/devices.py`
- `flask_app/app.py` (`_get_camera_model_info`)
