# Changelog - Support ESPHome 2025.10.0+

## Version 2.0.0 - 15 octobre 2025

### Ajouts majeurs

#### Composants ESPHome (`esphome_components/`)

Ajout d'une version locale et corrigée des composants ESPHome Madoka :

- **madoka/** : Composant climate pour ESP32
  - Copié depuis le fork Petapton/esphome@madoka
  - Compatible avec toutes les versions d'ESPHome

- **ble_client/** : Composant BLE client corrigé
  - **FIX CRITIQUE** : Wrapper `safe_consume_connection_slots()` pour ESPHome 2025.10.0+
  - Résout l'erreur `AttributeError: module 'esphome.components.esp32_ble_tracker' has no attribute 'consume_connection_slots'`
  - Rétrocompatible avec les anciennes versions d'ESPHome

### Documentation

- `esphome_components/README.md` : Guide d'utilisation complet
- `esphome_components/DEPLOYMENT.md` : Guide de déploiement détaillé
- `esphome_components/example-config.yaml` : Configuration exemple complète
- Mise à jour de `.github/copilot-instructions.md` avec les deux approches
- Mise à jour du `README.md` principal pour expliquer les deux options

### Changements dans la structure

```
daikin_madoka/
├── esphome_components/          # NOUVEAU
│   ├── madoka/                  # Composant climate ESP32
│   ├── ble_client/              # BLE client corrigé
│   ├── README.md
│   ├── DEPLOYMENT.md
│   └── example-config.yaml
├── .github/                     # NOUVEAU
│   └── copilot-instructions.md
├── __init__.py                  # Intégration Home Assistant
├── climate.py
├── config_flow.py
├── sensor.py
└── README.md                    # MODIFIÉ
```

## Migration

### Pour les utilisateurs d'ESPHome

**Avant :**
```yaml
external_components:
  - source: github://Petapton/esphome@madoka
    components: [ madoka, ble_client ]
```

**Après :**
```yaml
external_components:
  - source:
      type: local
      path: esphome_components  # Chemin local
    components: [ madoka, ble_client ]
```

### Pour les utilisateurs de l'intégration Home Assistant

Aucun changement requis. L'intégration fonctionne toujours de la même manière.

## Problèmes résolus

- ✅ ESPHome 2025.10.0+ compatibility
- ✅ `AttributeError` dans `esp32_ble_tracker.consume_connection_slots`
- ✅ Documentation manquante pour le déploiement ESPHome
- ✅ Absence de fichiers d'exemple de configuration

## Notes techniques

### Fonction de compatibilité

```python
def safe_consume_connection_slots(slots, component_name):
    """Wrapper pour consume_connection_slots compatible avec toutes les versions."""
    if hasattr(esp32_ble_tracker, 'consume_connection_slots'):
        return esp32_ble_tracker.consume_connection_slots(slots, component_name)
    else:
        return lambda config: config  # ESPHome 2025.10.0+
```

Cette fonction détecte automatiquement la version d'ESPHome et adapte le comportement.

## Crédits

- Intégration Home Assistant originale : mduran80
- Composant ESPHome Madoka : Petapton
- Correctifs ESPHome 2025.10.0+ : Ce repository
