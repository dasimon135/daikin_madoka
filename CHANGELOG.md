# Changelog

## v2.4.0 - July 2026

### HA Integration — "Modern Bluetooth" release

- **ESPHome Bluetooth proxy support**: connections now go exclusively through Home Assistant's Bluetooth stack (`bleak-retry-connector`), so the thermostat can be reached through any ESPHome Bluetooth proxy — the HA server no longer needs to be within BLE range. The Linux-only `bluetoothctl` shell-out at setup is gone (`force_update` entry option is now ignored).
- **Automatic discovery**: BRC1H thermostats advertised near HA are discovered and offered in the UI (`local_name: BRC1H*` matcher). The manual flow now shows a dropdown of discovered devices with a free-MAC fallback.
- **DataUpdateCoordinator**: one shared BLE poll per device instead of independent per-entity updates; entities become unavailable when polling fails; setup raises "not ready" (with automatic retry) when the device is unreachable.
- **Dual setpoint in AUTO mode**: when the device reports `range_enabled`, the climate entity exposes separate heating/cooling target temperatures (`TARGET_TEMPERATURE_RANGE`).
- **Device-reported temperature limits**: `min_temp`/`max_temp` are read from the thermostat's own setpoint limits when available (fallback 16–32 °C).
- **New entity**: Bluetooth signal strength (RSSI) diagnostic sensor, disabled by default.
- **Options flow**: configurable poll interval (10–600 s, default 60 s).
- **Diagnostics**: downloadable config-entry diagnostics with MAC redaction.
- **Device registry**: model, hardware and software versions from the device info characteristics.
- **Modern entity naming** (`has_entity_name` + translation keys) and a **French translation** (en/es/fr). Entity IDs and unique IDs are unchanged; displayed names may differ slightly.
- **pymadoka 0.3.0**: modern pyproject packaging (lean core: `bleak` + `bleak-retry-connector`; CLI/MQTT moved to extras), unit tests + CI, fix for a hang when the device was out of range at setup (swallowed task cancellation).
- Version bumped to 2.4.0; requires pymadoka 0.3.0.

No ESPHome changes. ESPHome users should keep `ref: v2.1.1`.

---

## v2.3.0 - Juin 2026

### HA Integration

- **Correctif `bleak`** : la lib `pymadoka` n'importe plus `discover` (supprimé dans bleak 0.20). L'intégration HA native fonctionne désormais sur les versions récentes de Home Assistant — l'option ESPHome n'est plus un contournement obligatoire. Merci à [@andreaippo](https://github.com/andreaippo) (PR #13 + pymadoka #30).
- **Chemin Bluetooth HA** : connexion via la pile Bluetooth de Home Assistant (`bleak_retry_connector`) avec reconnexion à backoff exponentiel ; ajout de `dependencies: ["bluetooth"]`. Le chemin standalone/CLI de pymadoka reste fonctionnel (`hass=None`).
- **Robustesse** : verrou par opération + timeout de 10 s sur chaque commande, garde anti-réentrance sur `start()`, `cleanup()` plus sûr.
- **Config flow par appareil** : un thermostat par entrée (`address` + `friendly_name`), `unique_id` = MAC. Corrige le blocage du flow avec plusieurs adresses MAC. Les entrées existantes (liste `devices`) restent prises en charge (rétro-compatibilité, pas de re-création nécessaire).
- **Nouvelle entité** : `number` pour la luminosité de la LED (eye brightness), 0–19 — parité avec le composant ESPHome.
- **Correctifs** : `async_unload_entry` libère désormais correctement les ressources (`stop()` des controllers, retour de l'état) ; `sensor` utilise `native_unit_of_measurement` ; `hacs.json` corrigé.
- Version bumped to 2.3.0 ; pymadoka 0.2.16.

No ESPHome changes. ESPHome users should keep `ref: v2.1.1`.

---

## v2.2.0 - Juin 2026

### HA Integration

- **Nouvelle entité** : `binary_sensor` pour l'alerte filtre (`device_class: problem`)
- **Nouvelle entité** : `button` pour réinitialiser le compteur filtre (`entity_category: diagnostic`)
- **Restructuration** : fichiers déplacés vers `custom_components/daikin_madoka/` (standard HACS — installation transparente pour les utilisateurs existants)
- **Parité ESPHome** : l'intégration HA directe expose désormais les mêmes entités filtre que le composant ESPHome
- Version bumped to 2.2.0

No ESPHome changes. ESPHome users should keep `ref: v2.1.1`.

---

## v2.1.1 - Avril 2026

### Fixes

- **ble_client**: declare `synchronous=True/False` on all 6 `register_action()` calls — removes ESPHome 2026.4 warnings about missing `synchronous=` parameter
- **madoka**: both `esphome/components/` and `esphome_components/` copies now use `add_feature_flags()` consistently — removes remaining `-Wdeprecated-declarations` compiler warnings

No behaviour change. Thanks to [@Dvorf](https://github.com/Dvorf) for identifying both issues.

---

## v2.1.0 - Avril 2026

### ESPHome

- **Nouvelles entités** : `outdoor_temperature`, `clean_filter`, `firmware_version`, `eye_brightness`, `reset_filter` exposées par le composant madoka
- **Suppression du `ble_client` local** : ESPHome 2026.4 gère nativement la gestion des connexions BLE, le composant local n'est plus nécessaire
- **Correction deprecations ESPHome 2026.4** : `ClimateTraits.set_supports_current_temperature()` et `set_supports_two_point_target_temperature()` remplacés par `add_feature_flags()`
- **Correction `AUTO_LOAD`** : ajout des dépendances `binary_sensor`, `button`, `number`, `sensor`, `text_sensor` dans `climate.py`
- **Script stop/start BLE amélioré** : ajout de `ble_client.disconnect` explicite en plus de `stop_scan()` pour libérer correctement le thermostat lors du ré-appairage téléphone
- **Reconnexion conditionnelle** : le `on_disconnect` ne relance la connexion que si le switch proxy est actif

### Compatibilité

Requiert **ESPHome 2025.10+**. Testé et validé sur ESPHome 2026.4.0.

---

## v2.0.0 - Octobre 2025

### Ajouts

- Composants ESPHome dans `esphome_components/madoka/`
- Support ESP32-S3 (M5Stack Atom S3 Lite)
- Documentation complète (README, exemple de configuration)
- Intégration HA directe (existante, inchangée)

### Crédits

- Intégration HA originale : [@mduran80](https://github.com/mduran80/daikin_madoka)
- Composant ESPHome madoka : [Petapton/esphome](https://github.com/Petapton/esphome)
- Support ESP32-S3 et switch ré-appairage : [@Quev1n](https://forum.hacf.fr)
