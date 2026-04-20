# Guide de déploiement - Composants ESPHome Madoka

## Prérequis

- ESPHome **2025.10+** (testé sur 2026.4)
- Un ESP32 ou ESP32-S3 (recommandé : M5Stack Atom Lite ou Atom S3 Lite)
- Thermostats Daikin Madoka BRC1H

## Étapes d'installation

### 1. Récupérer les composants

#### Option A : Depuis GitHub (recommandé)

```yaml
external_components:
  - source: github://dasimon135/daikin_madoka@main
    components: [ madoka ]
```

#### Option B : En local

Copiez le dossier `esphome_components/madoka/` dans votre dossier de configuration ESPHome, puis :

```yaml
external_components:
  - source:
      type: local
      path: esphome_components
    components: [ madoka ]
```

### 2. Créer votre fichier de configuration

```yaml
esphome:
  name: madoka-proxy

esp32:
  board: m5stack-atom  # Ajustez selon votre matériel

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

api:
  encryption:
    key: !secret api_key

ota:
  - platform: esphome

external_components:
  - source: github://dasimon135/daikin_madoka@main
    components: [ madoka ]

esp32_ble_tracker:
  id: ble_tracker
  max_connections: 2

bluetooth_proxy:
  active: false

ble_client:
  - mac_address: "XX:XX:XX:XX:XX:XX"
    id: madoka_1
    on_disconnect:
      then:
        - ble_client.connect: madoka_1

climate:
  - platform: madoka
    name: "Madoka Thermostat"
    ble_client_id: madoka_1
    update_interval: 15s
    outdoor_temperature:
      name: "Madoka Temp. Exterieure"
    clean_filter:
      name: "Madoka Filtre a Nettoyer"
    firmware_version:
      name: "Madoka Firmware"
    eye_brightness:
      name: "Madoka Luminosite LED"
    reset_filter:
      name: "Madoka Reset Filtre"
```

### 3. Compiler et flasher

Via le dashboard ESPHome : cliquer sur **INSTALL**.

Via ligne de commande :
```bash
esphome compile madoka-proxy.yaml
esphome upload madoka-proxy.yaml
```

## Dépannage

### Le thermostat ne se connecte pas

1. Vérifiez l'adresse MAC avec `bluetoothctl scan on`
2. Assurez-vous que le thermostat n'est pas connecté à l'application mobile
3. Rapprochez l'ESP32 du thermostat
4. Activez les logs DEBUG : `madoka: DEBUG`

### Ré-appairage avec le téléphone

Voir la section dédiée dans `README.md` — le switch BLE est inclus dans `example-config.yaml`.
