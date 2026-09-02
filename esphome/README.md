# Composants ESPHome Madoka

Ce dossier contient les composants ESPHome personnalisés pour contrôler les thermostats Daikin Madoka BRC1H via un proxy Bluetooth ESP32.

## Compatibilité

| ESPHome | Support |
|---|---|
| < 2025.10 | Non supporté |
| 2025.10 – 2026.3 | Compatible (non testé) |
| 2026.4+ | Testé et validé |

## Composants inclus

- **madoka** : Composant climate pour contrôler les thermostats Madoka
- **madoka_vam** : Composant climate pour les unités de ventilation Daikin VAM (VMC double flux / récupération de chaleur), pilotées par le même contrôleur BRC1H

## Installation

### Option 1 : Utilisation locale (recommandé)

Copiez le dossier `esphome_components` dans votre dossier de configuration ESPHome, puis dans votre fichier YAML :

```yaml
external_components:
  - source:
      type: local
      path: esphome_components
    components: [ madoka ]
```

### Option 2 : Depuis GitHub

Si vous hébergez ce repository sur GitHub :

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/dasimon135/daikin_madoka
      ref: v2.1.1
      path: esphome_components
    components: [ madoka ]
```

Le champ `path: esphome_components` est important dans ce dépôt pour charger la version maintenue du composant externe.

Ne pointez pas vos ESP32 sur `main` en production. Utilisez un tag Git validé pour éviter qu'une évolution de la partie Home Assistant ou d'un composant ESPHome casse vos déploiements existants.

## Politique de versions recommandée

- `main` : intégration continue, peut contenir des changements non encore validés sur tous les ESP32
- tags `vX.Y.Z` : versions stables recommandées pour ESPHome
- HACS : peut suivre les releases du dépôt sans imposer aux utilisateurs ESPHome de suivre `main`

Convention simple conseillée :

1. Taguer une version seulement après validation réelle sur matériel.
2. Documenter dans le changelog tout changement YAML ou comportemental côté ESPHome.
3. Demander aux utilisateurs ESPHome de mettre à jour explicitement leur `ref` quand ils veulent changer de version.

## Configuration exemple

```yaml
substitutions:
  name: m5stack-atom-lite-a03448
  friendly_name: AtomeBuanderie

esphome:
  name: ${name}
  friendly_name: ${friendly_name}

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

api:
  encryption:
    key: votre_clé_ici

ota:
  - platform: esphome

external_components:
  - source:
      type: local
      path: /config/esphome/esphome_components  # Ajustez le chemin selon votre config
    components: [ madoka ]

esp32_ble_tracker:
  max_connections: 2

bluetooth_proxy:
  active: false

ble_client:
  - mac_address: "F0:B3:1E:87:AF:FE"
    id: madoka_salon
    on_disconnect:
      then:
        - ble_client.connect: madoka_salon
  - mac_address: "1C:54:9E:90:E3:0E"
    id: madoka_parents
    on_disconnect:
      then:
        - ble_client.connect: madoka_parents

climate:
  - platform: madoka
    name: "Madoka salon"
    ble_client_id: madoka_salon
    update_interval: 15s
    # dual_setpoint: true   # a activer seulement si le mode plage est actif
    outdoor_temperature:
      name: "Madoka salon Temp. Exterieure"
    clean_filter:
      name: "Madoka salon Filtre a Nettoyer"
    firmware_version:
      name: "Madoka salon Firmware"
    eye_brightness:
      name: "Madoka salon Luminosite LED"
    reset_filter:
      name: "Madoka salon Reset Filtre"
  - platform: madoka
    name: "Madoka parents"
    ble_client_id: madoka_parents
    update_interval: 15s
    outdoor_temperature:
      name: "Madoka parents Temp. Exterieure"
    clean_filter:
      name: "Madoka parents Filtre a Nettoyer"
    firmware_version:
      name: "Madoka parents Firmware"
    eye_brightness:
      name: "Madoka parents Luminosite LED"
    reset_filter:
      name: "Madoka parents Reset Filtre"
```

## Consigne unique ou double (`dual_setpoint`)

Le BRC1H peut fonctionner avec une seule consigne ou avec une **plage**
chauffage/refroidissement (mode « range » activé sur le thermostat).

| Option | Defaut | Description |
|---|---|---|
| `dual_setpoint` | `false` | Expose deux consignes (plage chaud/froid) au lieu d'une seule. |

```yaml
climate:
  - platform: madoka
    name: "Madoka salon"
    ble_client_id: madoka_salon
    dual_setpoint: true   # uniquement si le mode plage est actif sur le BRC1H
```

**Pourquoi une option YAML et pas un choix automatique ?** L'integration Home
Assistant (option 1 du README principal) bascule seule entre consigne simple et
plage, parce que HA autorise une entite a changer ses `supported_features` a
l'execution. ESPHome ne le permet pas : les *traits* climate sont annonces une
seule fois, au moment ou l'ESP32 declare ses entites a l'API. Le choix doit donc
etre fait a la compilation. La parite avec l'integration native est donc au
niveau de la configuration, pas dynamique.

En mode simple, le composant ecrit le registre de consigne correspondant au mode
actif (chauffage en `heat`, refroidissement sinon) et renvoie l'autre registre a
sa derniere valeur lue sur le thermostat — meme regle que l'integration native.

> **Changement de comportement** : avant cette version, l'entite ESP32 annoncait
> toujours deux consignes. Apres mise a jour du composant externe, elle n'en
> expose plus qu'une, sauf si vous ajoutez `dual_setpoint: true`.

## Entites additionnelles du composant madoka

Chaque bloc `climate: - platform: madoka` peut maintenant exposer des entites auxiliaires :

- `outdoor_temperature`: capteur de temperature exterieure
- `clean_filter`: binary sensor indiquant qu'un nettoyage de filtre est necessaire
- `firmware_version`: text sensor de diagnostic pour la version firmware lue sur la telecommande
- `eye_brightness`: number (0-19) pour regler la luminosite de la LED facade
- `reset_filter`: button pour acquitter l'alerte filtre et reinitialiser le timer

## Composant madoka_vam (ventilation VAM)

Pour une unité de **ventilation Daikin VAM** (VMC double flux / récupération de
chaleur), utilisez la plateforme dédiée **`madoka_vam`** au lieu de `madoka`. Le
VAM est piloté par le même contrôleur BRC1H, sur le même service BLE, mais ne
fait que ventiler : mode d'opération `5` (VENTILATION), sans consigne de
température.

Entités exposées : modes **Off** / **Fan only**, vitesse de ventilation
(LOW/HIGH), preset de mode de ventilation (*Auto*, *Heat exchange*, *Bypass*),
température courante. Le VAM est une unité intérieure : il n'expose pas de
capteur de température extérieure. Options : `firmware_version` (text sensor),
`dump_raw` (booléen).

La vitesse et le mode de ventilation sont portés par la fonction BLE `0x0031`
(arguments `0x21` et `0x20`), et non par la fonction `0x0050` du thermostat.

```yaml
external_components:
  - source:
      type: local
      path: /config/esphome/esphome_components  # Ajustez le chemin selon votre config
    components: [ madoka_vam ]

esp32_ble_tracker:
  max_connections: 2

bluetooth_proxy:
  active: false

ble_client:
  - mac_address: "AA:BB:CC:DD:EE:FF"  # Adresse MAC de votre VAM
    id: vam_client
    on_disconnect:
      then:
        - ble_client.connect: vam_client

climate:
  - platform: madoka_vam
    name: "Ventilation VAM"
    ble_client_id: vam_client
    update_interval: 15s
    firmware_version:
      name: "VAM Firmware"
    dump_raw: false  # passez à true pour journaliser les trames BLE (reverse engineering)
```

Le drapeau `dump_raw: true` journalise en hexadécimal chaque trame BLE ainsi que
toute fonction inconnue reçue — utile pour cartographier les fonctions
spécifiques au VAM. Voir [../docs/reverse-engineering-vam.md](../docs/reverse-engineering-vam.md).

## Structure des fichiers

```
esphome_components/
├── madoka/
│   ├── __init__.py
│   ├── climate.py
│   ├── madoka.cpp
│   └── madoka.h
└── madoka_vam/
    ├── __init__.py
    ├── climate.py
    ├── madoka_vam.cpp
    └── madoka_vam.h
```

## Ré-appairage avec le téléphone

Lorsque l'ESP32 est actif, il tente de se connecter en boucle au Madoka. Si vous supprimez tous les appairages Bluetooth sur le thermostat pour reprendre la main avec l'application téléphone, l'ESP32 monopolise la connexion et empêche le téléphone de s'appairer à nouveau.

**Solution** : ajouter un switch dans ESPHome pour couper temporairement le scan BLE depuis Home Assistant.

**Étape 1** — Ajoutez un `id` au bloc `esp32_ble_tracker` :

```yaml
esp32_ble_tracker:
  id: ble_tracker
  max_connections: 2
```

**Étape 2** — Ajoutez le switch et les scripts :

```yaml
switch:
  - platform: template
    name: "Proxy Madoka Actif"
    id: proxy_enabled
    optimistic: true
    restore_mode: RESTORE_DEFAULT_ON
    turn_on_action:
      - script.execute: start_ble
    turn_off_action:
      - script.execute: stop_ble

script:
  - id: stop_ble
    then:
      - logger.log: "Arret BLE - scan stop et deconnexion des thermostats"
      - lambda: |-
          id(ble_tracker).stop_scan();
      - ble_client.disconnect: madoka_salon
      - ble_client.disconnect: madoka_chambre

  - id: start_ble
    then:
      - logger.log: "Demarrage BLE - reprise du scan et reconnexion ESP32"
      - lambda: |-
          id(ble_tracker).start_scan();
      - ble_client.connect: madoka_salon
      - ble_client.connect: madoka_chambre
```

**Procédure de ré-appairage :**
1. Dans Home Assistant, passez le switch **"Proxy Madoka Actif"** sur **OFF**
2. Appairez votre téléphone avec le Madoka et effectuez vos modifications
3. Repassez le switch sur **ON** — l'ESP32 se reconnecte automatiquement

> Important : `stop_scan()` seul ne suffit pas toujours. La vraie libération du thermostat pour l'application téléphone vient des actions `ble_client.disconnect`.

> Le switch est déjà inclus dans le fichier `example-config.yaml`.

## Dépannage

### Le composant ne se charge pas

Vérifiez que le chemin dans `external_components.source.path` pointe correctement vers le dossier `esphome_components`.

## Crédits

- Composant madoka original : [Petapton/esphome](https://github.com/Petapton/esphome)
