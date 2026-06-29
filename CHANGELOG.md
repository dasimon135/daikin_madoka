# Changelog

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
