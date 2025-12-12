# Guide de test rapide - ESPHome Madoka

## Test 1 : Vérifier la structure des fichiers

```powershell
# Vérifier que tous les fichiers sont présents
tree /F c:\Users\dasim\repos\daikin_madoka\esphome_components
```

Vous devriez voir :
- `esphome_components/ble_client/__init__.py` (avec le fix)
- `esphome_components/madoka/*` (4 fichiers)
- `esphome_components/README.md`
- `esphome_components/DEPLOYMENT.md`
- `esphome_components/example-config.yaml`

## Test 2 : Vérifier le fix dans ble_client

```powershell
# Rechercher la fonction de compatibilité
Select-String -Path "c:\Users\dasim\repos\daikin_madoka\esphome_components\ble_client\__init__.py" -Pattern "safe_consume_connection_slots"
```

Devrait afficher au moins 2 lignes (définition + utilisation).

## Test 3 : Copier vers votre config ESPHome

### Si vous utilisez Home Assistant avec add-on ESPHome :

```powershell
# Copier les composants vers votre config Home Assistant
# REMPLACEZ <path_to_ha> par votre chemin HA
Copy-Item -Recurse c:\Users\dasim\repos\daikin_madoka\esphome_components "\\<ha_server>\config\esphome\"
```

### Si vous utilisez ESPHome standalone :

```powershell
# Copier vers votre dossier ESPHome local
Copy-Item -Recurse c:\Users\dasim\repos\daikin_madoka\esphome_components <votre_chemin_esphome>
```

## Test 4 : Adapter votre configuration YAML

Modifiez votre fichier `m5stack-atom-lite-a03448.yaml` :

**ANCIEN :**
```yaml
external_components:
  - source: github://Petapton/esphome@madoka
    components: [ madoka, ble_client ]
```

**NOUVEAU :**
```yaml
external_components:
  - source:
      type: local
      path: esphome_components  # Chemin relatif depuis /config/esphome/
    components: [ madoka, ble_client ]
```

## Test 5 : Compiler la configuration

### Via Home Assistant UI :
1. Ouvrir ESPHome dashboard
2. Cliquer sur votre device `m5stack-atom-lite-a03448`
3. Cliquer sur "VALIDATE" (ou "INSTALL" pour compiler)

### Via CLI :
```bash
esphome compile m5stack-atom-lite-a03448.yaml
```

### Résultat attendu :
✅ Compilation réussie sans erreur `AttributeError`

## Test 6 : Vérifier les logs

Une fois flashé, vérifiez les logs :

```bash
esphome logs m5stack-atom-lite-a03448.yaml
```

Recherchez :
```
[I][app:102]: ESPHome version 2025.10.0 compiled on ...
[C][ble_client:xxx]: BLE Client:
[C][madoka.climate:xxx]: Madoka Climate:
[D][ble_client:xxx]: Connected to F0:B3:1E:87:AF:FE
```

## Dépannage rapide

### Erreur : "Could not find component ble_client"

**Cause** : Le chemin `path:` est incorrect

**Solution** :
```yaml
# Si vous êtes dans /config/esphome/ et les composants sont dans /config/esphome/esphome_components/
path: esphome_components

# Si vous êtes ailleurs, utilisez le chemin absolu
path: /config/esphome/esphome_components
```

### Erreur persiste : `AttributeError: consume_connection_slots`

**Cause** : Les anciens fichiers sont peut-être en cache

**Solution** :
```bash
# Supprimer le cache ESPHome
rm -rf /config/esphome/.esphome/

# Ou via Home Assistant
rm -rf /config/.esphome/
```

### Le thermostat ne se connecte pas

**Vérifications** :
1. MAC address correcte dans la config ?
2. Thermostat pas connecté à l'app mobile ?
3. ESP32 suffisamment proche du thermostat ?

**Test** :
```yaml
logger:
  level: VERBOSE  # Active tous les logs BLE
```

## Validation finale

✅ Configuration compile sans erreur  
✅ ESP32 flashé avec succès  
✅ Logs montrent "Connected to ..."  
✅ Entité climate visible dans Home Assistant  
✅ Contrôle température fonctionne  

## Support

En cas de problème, vérifiez :
1. `esphome_components/README.md` - Documentation complète
2. `esphome_components/DEPLOYMENT.md` - Guide de déploiement
3. `esphome_components/example-config.yaml` - Configuration de référence
4. `.github/copilot-instructions.md` - Informations techniques

Bonne chance ! 🎉
