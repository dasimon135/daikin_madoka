# Annonce HACF — v2.4.0 (à poster dans le fil du tuto)

> Fil : https://forum.hacf.fr/t/tuto-controler-un-thermostat-daikin-madoka-brc1h-via-bluetooth-avec-home-assistant-integration-custom-esphome/75688

---

## 🚀 Daikin Madoka v2.4.0 — « Modern Bluetooth » : proxys ESPHome, découverte auto, et plus besoin d'être à portée !

Bonjour à tous,

Grosse mise à jour de l'intégration **Daikin Madoka (BRC1H)** : la [v2.4.0](https://github.com/dasimon135/daikin_madoka/releases/tag/v2.4.0) est disponible via HACS. C'est la plus grosse évolution depuis le début du projet, **validée de bout en bout sur du vrai matériel** (mes propres thermostats 😄).

### ✨ Les nouveautés

- 📡 **Fonctionne via les proxys Bluetooth ESPHome** : votre serveur HA n'a **plus besoin d'être à portée BLE** du thermostat. Un Atom/ESP32 en proxy dans la pièce suffit — fini la contrainte qui obligeait beaucoup d'entre vous à passer par l'Option 2.
- 🔍 **Découverte automatique** : les BRC1H à portée sont détectés et proposés directement dans l'UI (« Daikin Madoka détecté »), plus besoin de taper la MAC.
- 🔄 **Reconnexion auto-réparante** : une coupure BLE se répare toute seule au poll suivant, sans recharger l'intégration.
- 🌡️ **Double consigne en mode AUTO** (chaud/froid séparés) si le mode plage est activé sur le thermostat, et bornes min/max lues **de l'appareil**.
- 🇫🇷 **Traduction française complète** (entités, configuration, messages d'erreur).
- 🎨 **Icône dédiée** (le cadran du BRC1H avec son halo violet, affiché sur HA ≥ 2026.3), capteur RSSI, intervalle de poll réglable, diagnostics téléchargeables, erreurs visibles quand une commande échoue.

### ⚠️ Important si vous passez par un proxy Bluetooth

Le BRC1H exige un **appairage authentifié (MITM)** et ignore silencieusement les clients non appairés. Le firmware bluetooth-proxy « standard » d'ESPHome **ne sait pas faire cet appairage** : il faut ajouter quelques lignes au YAML de votre proxy (io_capability + un répondeur d'appairage) — tout est documenté dans la [section Requirements du README](https://github.com/dasimon135/daikin_madoka#requirements), avec la config exacte validée. À la première connexion, acceptez la demande d'appairage sur l'écran du thermostat (une fois **par proxy**).

### ⬆️ Mise à jour depuis la 2.3.x

Mise à jour HACS + redémarrage, c'est tout :
- Les `entity_id` et l'historique sont **conservés** (automations et dashboards intacts) ; seuls les noms affichés changent légèrement.
- Les anciennes entrées de config continuent de fonctionner ; les options `adapter`/`force_update` sont désormais ignorées (tout passe par la pile Bluetooth de HA).
- L'appairage `bluetoothctl` existant sur adaptateur local reste valable.
- Utilisateurs **ESPHome (Option 2)** : rien ne change pour vous, gardez votre `ref` épinglé.

### 🔮 La suite (v3.0)

Au programme : une **carte Lovelace dédiée** au design du BRC1H (cadran rond + halo), un assistant de configuration qui guide l'appairage en direct, des statistiques de fonctionnement, et la publication de la lib sur PyPI. La feuille de route est [dans le repo](https://github.com/dasimon135/daikin_madoka/blob/main/docs/plans/2026-07-15-v3-roadmap.md).

Retours bienvenus, ici ou sur [GitHub](https://github.com/dasimon135/daikin_madoka/issues) ! Si la mise à jour vous pose le moindre souci, postez vos logs (Paramètres → Appareils → Daikin Madoka → Télécharger les diagnostics).

Bonne clim' à tous ! ❄️🔥
