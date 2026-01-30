# Composants ESPHome Madoka

Ce dossier contient les composants ESPHome personnalisés pour contrôler les thermostats Daikin Madoka BRC1H via un proxy Bluetooth ESP32.

## Composants inclus

- **madoka** : Composant climate pour contrôler les thermostats Madoka
- **ble_client** : Version corrigée du composant ble_client compatible avec ESPHome 2025.10.0+

## Installation

### Option 1 : Utilisation locale (recommandé)

Copiez le dossier `esphome_components` dans votre dossier de configuration ESPHome, puis dans votre fichier YAML :

```yaml
external_components:
  - source:
      type: local
      path: esphome_components
    components: [ madoka, ble_client ]
```

### Option 2 : Depuis GitHub

Si vous hébergez ce repository sur GitHub :

```yaml
external_components:
  - source: github://votre-utilisateur/daikin_madoka
    components: [ madoka, ble_client ]
```

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

external_components:
  - source:
      type: local
      path: /config/esphome/esphome_components  # Ajustez le chemin selon votre config
    components: [ madoka, ble_client ]

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
  - platform: madoka
    name: "Madoka parents"
    ble_client_id: madoka_parents
    update_interval: 15s
```

## Compatibilité ESP32-S3 (M5Stack Atom S3 Lite)

Le composant Madoka est compatible avec les ESP32-S3, notamment le **M5Stack Atom S3 Lite**. Cette compatibilité a nécessité plusieurs modifications importantes.

### Configuration requise pour ESP32-S3

Les thermostats Daikin Madoka utilisent un **pairing BLE sécurisé avec Numeric Comparison** (un code à 6 chiffres s'affiche sur les deux appareils). Pour que cela fonctionne sur ESP32-S3, vous devez configurer l'IO Capability dans votre YAML :

```yaml
esp32_ble:
  io_capability: display_yes_no  # OBLIGATOIRE pour le pairing Madoka
```

### Exemple de configuration ESP32-S3 complète

```yaml
esphome:
  name: atomebuanderie
  friendly_name: AtomeBuanderie
  platformio_options:
    board_build.flash_mode: dio

esp32:
  board: m5stack-atoms3
  variant: esp32s3
  framework:
    type: esp-idf
    version: recommended
    sdkconfig_options:
      CONFIG_BT_BLE_50_FEATURES_SUPPORTED: y
      CONFIG_BT_BLE_42_FEATURES_SUPPORTED: y

esp32_ble:
  io_capability: display_yes_no  # Pour le pairing Numeric Comparison

esp32_ble_tracker:
  scan_parameters:
    interval: 320ms
    window: 30ms
    active: true
    continuous: true

bluetooth_proxy:
  active: false  # Désactiver le proxy quand on utilise ble_client directement

external_components:
  - source: github://dasimon135/daikin_madoka@madoka
    components: [ madoka, ble_client ]

ble_client:
  - mac_address: "F0:B3:1E:87:AF:FE"
    id: madoka_salon
    on_disconnect:
      then:
        - ble_client.connect: madoka_salon

climate:
  - platform: madoka
    name: "Madoka salon"
    ble_client_id: madoka_salon
    update_interval: 15s
```

### Processus de pairing

1. **Au premier appairage**, un code à 6 chiffres s'affiche dans les logs ESPHome :
   ```
   ╔══════════════════════════════════════════════════════════╗
   ║  PAIRING CODE: 790440                                   ║
   ║  Vérifiez que ce code correspond à celui sur le Madoka  ║
   ║  et CONFIRMEZ sur le thermostat!                        ║
   ╚══════════════════════════════════════════════════════════╝
   ```

2. **Le même code s'affiche sur l'écran du thermostat Madoka**

3. **Confirmez sur le thermostat Madoka** en appuyant sur OK/Valider

4. L'ESP32 confirme automatiquement de son côté

5. Une fois appairé, les connexions suivantes sont automatiques

### Modifications techniques pour ESP32-S3

#### madoka.cpp

- **Configuration sécurité BLE** : Ajout de `esp_ble_gap_set_security_param()` pour MITM + Bonding
- **GAP Event Handler** : Gestion des événements de pairing (NC_REQ, AUTH_CMPL, KEY)
- **GATTC Event Handler** : Gestion de l'authentification insuffisante (erreur 0x52)
- **Auto-confirmation** : Le code de pairing est automatiquement confirmé côté ESP32
- **Retry logic** : 3 tentatives d'encryption en cas d'échec

#### madoka.h

- Ajout de flags d'état : `encryption_established_`, `services_discovered_`, `auth_retry_count_`
- Utilisation de l'API ClimateTraits compatible (avec warnings de dépréciation acceptés)

#### ble_client/ble_client.cpp & automation.h

- Correction de `address_str().c_str()` → `address_str()` pour compatibilité ESP-IDF 5.x

## Correctifs de compatibilité ESPHome

### ble_client/__init__.py

Ajout d'une fonction de compatibilité `safe_consume_connection_slots()` qui :
- Utilise `esp32_ble_tracker.consume_connection_slots()` si disponible (anciennes versions)
- Retourne une fonction no-op si la fonction n'existe pas (ESPHome 2025.10.0+)

Cela permet la compatibilité avec toutes les versions d'ESPHome.

## Structure des fichiers

```
esphome_components/
├── ble_client/
│   ├── __init__.py          # Version corrigée compatible ESPHome 2025.10.0+
│   ├── ble_client.cpp
│   └── ble_client.h
└── madoka/
    ├── __init__.py
    ├── climate.py
    ├── madoka.cpp
    └── madoka.h
```

## Dépannage

### Erreur `AttributeError: module 'esphome.components.esp32_ble_tracker' has no attribute 'consume_connection_slots'`

Cette erreur se produit avec ESPHome 2025.10.0+ quand on utilise l'ancien composant ble_client. La version corrigée dans ce dossier résout ce problème.

### Le composant ne se charge pas

Vérifiez que le chemin dans `external_components.source.path` pointe correctement vers le dossier `esphome_components`.

### ESP32-S3 : Pairing échoue avec erreur 0x52 (AUTH_FAIL)

Cette erreur indique une authentification insuffisante. Vérifiez que :
1. `esp32_ble: io_capability: display_yes_no` est présent dans votre YAML
2. Vous confirmez le code de pairing sur le thermostat Madoka
3. L'ancien appairage n'est pas corrompu (effacez la NVS si nécessaire)

### ESP32-S3 : "IO Capability: none" dans les logs

Si les logs affichent `IO Capability: none` au lieu de `display_yes_no`, la configuration `esp32_ble` n'est pas appliquée. Vérifiez votre YAML.

### Plusieurs thermostats : le 2ème ne se connecte pas

Les thermostats se connectent un par un. Attendez que le premier soit appairé, puis le second tentera sa connexion. Chaque thermostat nécessite une confirmation de pairing séparée.

## Plateformes testées

| Plateforme | ESP32 | Framework | Statut |
|------------|-------|-----------|--------|
| M5Stack Atom Lite | ESP32 | ESP-IDF | ✅ Fonctionne |
| M5Stack Atom S3 Lite | ESP32-S3 | ESP-IDF 5.x | ✅ Fonctionne |
| Generic ESP32 | ESP32 | Arduino/ESP-IDF | ✅ Fonctionne |
| Generic ESP32-S3 | ESP32-S3 | ESP-IDF 5.x | ✅ Fonctionne |

## Crédits

- Composant madoka original : [Petapton/esphome](https://github.com/Petapton/esphome)
- Correctifs de compatibilité : Ce repository
