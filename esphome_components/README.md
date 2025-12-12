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

## Correctifs appliqués

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

## Crédits

- Composant madoka original : [Petapton/esphome](https://github.com/Petapton/esphome)
- Correctifs de compatibilité : Ce repository
