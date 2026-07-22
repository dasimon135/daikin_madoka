# Contrôler un thermostat Daikin Madoka BRC1H via Bluetooth avec Home Assistant — Custom Integration ou ESPHome

> Publié sur [HACF](https://forum.hacf.fr/t/tuto-controler-un-thermostat-daikin-madoka-brc1h-via-bluetooth-avec-home-assistant-integration-custom-esphome/75688)

## Fonctionnalités

- Contrôle complet : mode (chauffe, froid, auto, ventilation, déshumidification), consigne de température, vitesse de ventilation
- Lecture de la température ambiante via un capteur dédié
- Modes ventilateur : Auto, Low, Mid, High
- Plage de température : 16°C – 32°C
- Reconnexion automatique en cas de déconnexion BLE
- Compatible ESP32 et ESP32-S3 (M5Stack Atom Lite / Atom S3 Lite)
- Entités supplémentaires : température extérieure, alerte filtre, firmware, luminosité LED, reset filtre

---

## Option 1 : Intégration custom Home Assistant (Bluetooth direct)

> ✅ **Corrigé en v2.3.0** : les versions antérieures échouaient sur les HA récents avec `cannot import name 'discover' from 'bleak'` (bleak ≥ 0.20 a supprimé `discover`). La v2.3.0 embarque un fork [pymadoka](https://github.com/dasimon135/pymadoka) corrigé et passe par la pile Bluetooth de HA — l'Option 1 fonctionne donc sur les HA actuels. Si vous rencontrez cette erreur, mettez à jour vers **v2.3.0**.

**Prérequis** : Home Assistant avec accès Bluetooth, thermostat à moins de ~10m.

### Installation

**Via HACS (recommandé)** : ajoutez `https://github.com/dasimon135/daikin_madoka` comme dépôt custom de type *Intégration*, installez **Daikin Madoka**, redémarrez HA.

**Manuellement** : copiez le dossier `custom_components/daikin_madoka/` dans le répertoire `custom_components/` de votre config HA, redémarrez.

### Appairage Bluetooth

Le thermostat doit être appairé manuellement avec le serveur HA :

```bash
bluetoothctl
agent KeyboardDisplay
remove <MAC_DU_MADOKA>
scan on
# Attendre que le MAC apparaisse
scan off
pair <MAC_DU_MADOKA>
# Accepter le code sur le thermostat
```

### Configuration HA

Paramètres > Intégrations > Ajouter > **Daikin Madoka**

Renseigner :
- Adresse MAC Bluetooth du BRC1H
- Nom de l'adaptateur Bluetooth (généralement `hci0`)

### Entités créées

| Entité | Type | Description |
|---|---|---|
| `climate.*` | Climate | Contrôle principal (mode, consigne, ventilateur) |
| `sensor.*_indoor_temperature` | Sensor | Température intérieure |
| `sensor.*_outdoor_temperature` | Sensor | Température extérieure |
| `binary_sensor.*_clean_filter` | Binary sensor | Alerte nettoyage filtre |
| `button.*_reset_filter` | Button | Acquittement alerte filtre |
| `number.*_eye_brightness` | Number | Luminosité LED façade (0–19) |

### Docker / VM

```yaml
volumes:
  - /var/run/dbus/system_bus_socket:/var/run/dbus/system_bus_socket
privileged: true
```

---

## Option 2 : Proxy ESP32 via ESPHome (recommandé)

**Avantages** :
- Le serveur HA n'a pas besoin d'être à portée Bluetooth
- Fonctionne en Docker/VM sans configuration DBUS
- Un seul ESP32 gère plusieurs thermostats
- Fiabilité excellente

**Matériel testé** :
- M5Stack Atom Lite (ESP32)
- M5Stack Atom S3 Lite (ESP32-S3)
- ESP32 DevKit générique

**Prérequis** : ESPHome 2025.10+

### Configuration pour ESP32-S3 (ex: M5Stack Atom S3 Lite)

```yaml
esphome:
  name: madoka-proxy
  friendly_name: Madoka Proxy

esp32:
  board: m5stack-atoms3
  variant: esp32s3
  framework:
    type: esp-idf
    sdkconfig_options:
      CONFIG_BT_ENABLED: y
      CONFIG_BT_BLE_SMP_ENABLE: y
      CONFIG_BT_BLE_42_FEATURES_SUPPORTED: y
      CONFIG_BT_NIMBLE_ENABLED: n
      CONFIG_BTDM_CTRL_MODE_BLE_ONLY: y
      CONFIG_BT_BLE_DYNAMIC_ENV_MEMORY: y
      CONFIG_BT_STACK_NO_LOG: n

logger:
  level: DEBUG
  logs:
    madoka: DEBUG
    ble_client: DEBUG

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

api:
  encryption:
    key: !secret api_key

ota:
  - platform: esphome

# OBLIGATOIRE sur ESP32-S3 pour le pairing MITM
esp32_ble:
  io_capability: display_yes_no

external_components:
  - source:
      type: git
      url: https://github.com/dasimon135/daikin_madoka
      ref: v2.2.0
      path: esphome/components
    components: [madoka]

esp32_ble_tracker:
  id: ble_tracker
  scan_parameters:
    interval: 320ms
    window: 30ms
    active: true

ble_client:
  - mac_address: "XX:XX:XX:XX:XX:XX"  # Remplacez par votre adresse MAC
    id: madoka_salon
    on_disconnect:
      then:
        - if:
            condition:
              switch.is_on: proxy_enabled
            then:
              - delay: 10s
              - ble_client.connect: madoka_salon

climate:
  - platform: madoka
    name: "Madoka Salon"
    ble_client_id: madoka_salon
    update_interval: 15s
    outdoor_temperature:
      name: "Madoka Salon Temp. Exterieure"
    clean_filter:
      name: "Madoka Salon Filtre a Nettoyer"
    firmware_version:
      name: "Madoka Salon Firmware"
    eye_brightness:
      name: "Madoka Salon Luminosite LED"
    reset_filter:
      name: "Madoka Salon Reset Filtre"
```

### Configuration pour ESP32 classique (ex: M5Stack Atom Lite)

```yaml
esphome:
  name: madoka-proxy
  friendly_name: Madoka Proxy

esp32:
  board: m5stack-atom
  framework:
    type: esp-idf

logger:

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

api:
  encryption:
    key: !secret api_key

ota:
  - platform: esphome

external_components:
  - source:
      type: git
      url: https://github.com/dasimon135/daikin_madoka
      ref: v2.2.0
      path: esphome/components
    components: [madoka]

esp32_ble_tracker:
  id: ble_tracker
  scan_parameters:
    interval: 320ms
    window: 30ms
    active: true

ble_client:
  - mac_address: "XX:XX:XX:XX:XX:XX"
    id: madoka_1
    on_disconnect:
      then:
        - delay: 10s
        - ble_client.connect: madoka_1

climate:
  - platform: madoka
    name: "Madoka"
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

### Processus d'appairage ESP32 ↔ Madoka

1. Flasher l'ESP32 avec la configuration ci-dessus
2. Ouvrir les **logs ESPHome** (dashboard ESPHome > Logs)
3. Sur le thermostat, supprimer tous les appairages existants (menu Bluetooth du Madoka)
4. Un code à 6 chiffres apparaît simultanément dans les logs et sur l'écran du thermostat
5. Valider des deux côtés — l'appairage est mémorisé en NVS, il est persistant après redémarrage

### Entités créées dans Home Assistant

Chaque thermostat expose :

| Entité | Type | Description |
|---|---|---|
| `climate.madoka_*` | Climate | Contrôle principal (mode, consigne, ventilateur) |
| `sensor.madoka_*_temp_exterieure` | Sensor | Température extérieure |
| `binary_sensor.madoka_*_filtre_a_nettoyer` | Binary sensor | Alerte nettoyage filtre |
| `text_sensor.madoka_*_firmware` | Text sensor | Version firmware |
| `number.madoka_*_luminosite_led` | Number | Luminosité LED façade (0–19) |
| `button.madoka_*_reset_filtre` | Button | Acquittement alerte filtre |

### Pinning de version

Utilisez toujours un tag git stable — ne pointez jamais sur `main` en production :

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/dasimon135/daikin_madoka
      ref: v2.2.0        # remplacer par le tag le plus récent
      path: esphome/components
    components: [madoka]
```

Consultez le [CHANGELOG](../CHANGELOG.md) pour les versions disponibles.

---

## Ré-appairage avec le téléphone

**Problème** : quand on supprime tous les appairages sur le Madoka pour reprendre la main avec l'application téléphone, l'ESP32 tente de se reconnecter en boucle et empêche le téléphone de s'appairer.

**Solution** : ajouter un switch dans ESPHome pour couper temporairement le BLE depuis Home Assistant.

```yaml
esp32_ble_tracker:
  id: ble_tracker   # ← requis
  scan_parameters:
    interval: 320ms
    window: 30ms
    active: true

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
      # Ajoutez une ligne par thermostat supplémentaire

  - id: start_ble
    then:
      - logger.log: "Demarrage BLE - reprise du scan et reconnexion"
      - lambda: |-
          id(ble_tracker).start_scan();
      - ble_client.connect: madoka_salon
```

> `stop_scan()` seul ne suffit pas toujours — le `ble_client.disconnect` explicite est nécessaire pour libérer réellement le thermostat.

**Procédure** :
1. Dans HA, passer le switch **"Proxy Madoka Actif"** sur **OFF**
2. Appairer le téléphone avec le Madoka
3. Repasser sur **ON** — l'ESP32 se reconnecte automatiquement

*Solution originale par [@Quev1n](https://forum.hacf.fr)*

---

## Dépannage

| Erreur | Cause | Solution |
|---|---|---|
| `cannot import name 'discover' from 'bleak'` | Ancienne version (< v2.3.0) | Mettre à jour vers v2.3.0+ (fork pymadoka corrigé) — ou utiliser l'Option 2 |
| `Invalid handler specified` | Mauvais chemin d'installation ou HA non redémarré | Vérifier `/config/custom_components/daikin_madoka/`, redémarrer HA |
| Thermostat non trouvé | Connecté à l'appli mobile ou non appairé | Supprimer l'appairage sur le Madoka, re-scanner |
| Connexion instable | Signal BLE faible | Rapprocher l'ESP32 du thermostat |
| Code d'appairage non affiché | `io_capability` manquant | Ajouter `esp32_ble: io_capability: display_yes_no` (ESP32-S3) |
| `warned took a long time (400ms+)` | Normal — protocole BLE Madoka lent par nature | Ignorer, pas d'impact fonctionnel |

---

## Crédits

- Intégration HA originale : [@mduran80](https://github.com/mduran80/daikin_madoka) / bibliothèque [pymadoka](https://github.com/IgnacioHR/pymadoka)
- Composant ESPHome madoka : [Petapton/esphome](https://github.com/Petapton/esphome)
- Switch ré-appairage : [@Quev1n](https://forum.hacf.fr)
- Maintenance, entités additionnelles, compatibilité ESPHome 2026.4 : [dasimon135/daikin_madoka](https://github.com/dasimon135/daikin_madoka)
