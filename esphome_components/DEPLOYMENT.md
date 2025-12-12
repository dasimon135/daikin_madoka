# Guide de déploiement - Composants ESPHome Madoka

## Prérequis

- ESPHome 2024.x ou 2025.x installé
- Un ESP32 (recommandé: M5Stack Atom Lite)
- Thermostats Daikin Madoka BRC1H appairés

## Étapes d'installation

### 1. Copier les composants

#### Si vous utilisez Home Assistant avec add-on ESPHome:

```bash
# Depuis votre machine locale
scp -r esphome_components/* hassio:/config/esphome/esphome_components/
```

#### Si vous utilisez ESPHome en standalone:

```bash
# Copier dans votre dossier de configuration ESPHome
cp -r esphome_components /path/to/your/esphome/config/
```

### 2. Créer votre fichier de configuration

Créez un nouveau fichier YAML (ex: `madoka-proxy.yaml`) dans votre dossier ESPHome :

```yaml
substitutions:
  name: madoka-ble-proxy
  friendly_name: Madoka Proxy

esphome:
  name: ${name}
  friendly_name: ${friendly_name}

# Ajoutez votre plateforme ESP32
esp32:
  board: m5stack-atom  # Ajustez selon votre matériel
  framework:
    type: arduino

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

logger:

api:
  encryption:
    key: !secret api_key

ota:

# Composants locaux corrigés
external_components:
  - source:
      type: local
      path: esphome_components
    components: [ madoka, ble_client ]

esp32_ble_tracker:
  max_connections: 2

bluetooth_proxy:
  active: false

ble_client:
  - mac_address: "XX:XX:XX:XX:XX:XX"  # Votre adresse MAC
    id: madoka_1
    on_disconnect:
      then:
        - ble_client.connect: madoka_1

climate:
  - platform: madoka
    name: "Madoka Thermostat"
    ble_client_id: madoka_1
    update_interval: 15s
```

### 3. Compiler et flasher

#### Via Home Assistant Add-on:

1. Ouvrir l'interface ESPHome
2. Cliquer sur "INSTALL"
3. Choisir "Wireless" ou "Plug into this computer"

#### Via ligne de commande:

```bash
# Compiler
esphome compile madoka-proxy.yaml

# Flasher (première fois, via USB)
esphome upload madoka-proxy.yaml --device /dev/ttyUSB0

# Flasher (via OTA après la première fois)
esphome upload madoka-proxy.yaml
```

### 4. Vérifier les logs

```bash
esphome logs madoka-proxy.yaml
```

Vous devriez voir :
```
[D][ble_client:xxx]: Connected to XX:XX:XX:XX:XX:XX
[D][climate:xxx]: Madoka Thermostat - State update
```

## Dépannage

### Erreur: `consume_connection_slots`

Si vous voyez cette erreur, vérifiez que :
1. Vous utilisez bien les composants du dossier `esphome_components`
2. Le chemin `path:` dans `external_components` est correct
3. Le fichier `esphome_components/ble_client/__init__.py` contient bien la fonction `safe_consume_connection_slots`

### Le thermostat ne se connecte pas

1. Vérifiez l'adresse MAC avec `bluetoothctl scan on`
2. Assurez-vous que le thermostat n'est pas connecté à l'application mobile
3. Essayez de "forget" le device sur le thermostat et re-scanner
4. Augmentez le niveau de log à `DEBUG` pour plus de détails

### Connexions multiples instables

- Réduisez `max_connections` à 1 si vous n'avez qu'un thermostat
- Augmentez `update_interval` à 30s ou plus
- Vérifiez la qualité du signal Bluetooth (rapprochez l'ESP32)

## Migration depuis GitHub external_components

Si vous utilisiez précédemment :

```yaml
external_components:
  - source: github://Petapton/esphome@madoka
    components: [ madoka, ble_client ]
```

Remplacez simplement par :

```yaml
external_components:
  - source:
      type: local
      path: esphome_components
    components: [ madoka, ble_client ]
```

Et ajoutez les fichiers de ce repository dans votre dossier ESPHome.

## Maintenance

### Mettre à jour les composants

1. Récupérez la dernière version depuis GitHub
2. Remplacez le dossier `esphome_components`
3. Recompilez votre firmware ESPHome

### Revenir à une ancienne version d'ESPHome

Si vous devez utiliser une version < 2025.10.0, les composants restent compatibles grâce au wrapper `safe_consume_connection_slots()`.
