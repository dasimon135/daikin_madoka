# Annonce HACF — v3.2.0 (à poster dans le fil du tuto)

> Fil : https://forum.hacf.fr/t/tuto-controler-un-thermostat-daikin-madoka-brc1h-via-bluetooth-avec-home-assistant-integration-custom-esphome/75688

---

## 🛡️ Daikin Madoka v3.2.0 — robustesse multi-proxy : fini les thermostats qui tombent sans explication

Bonjour à tous,

La [v3.2.0](https://github.com/dasimon135/daikin_madoka/releases/tag/v3.2.0) de l'intégration **Daikin Madoka (BRC1H)** est disponible. C'est une release entièrement dédiée à la **fiabilité dans les maisons qui ont plusieurs proxys Bluetooth et plusieurs thermostats** — validée sur du vrai matériel (4 thermostats, 4 proxys), y compris en rejouant en direct la panne qui a motivé tout le chantier. 😅

### 🩺 Le problème qu'elle règle

Le BRC1H mémorise son appairage **par proxy** (un « bond » par Atom/ESP32), mais Home Assistant choisit le proxy **au signal le plus fort**. Quand les deux ne sont pas d'accord — un proxy proche mais jamais appairé gagne la connexion — le thermostat refuse le lien (`Insufficient authentication`)… et jusqu'ici, tout ce que vous voyiez, c'était une entité `indisponible` sans aucune explication. Impossible à diagnostiquer sans plonger dans les logs.

### ✨ Ce qui change

- 🧲 **Proxy « collant »** : l'intégration mémorise le proxy qui a réussi la dernière connexion authentifiée et le retente en premier. Un proxy plus proche mais non appairé ne peut plus voler la connexion et faire tomber le thermostat.
- 🔧 **Réparation `pairing_required`** : quand tous les chemins refusent la connexion faute d'appairage, une réparation actionnable apparaît qui **nomme les proxys fautifs** — vous savez exactement lequel appairer (ou passer en passif). Elle s'efface toute seule dès que ça remarche.
- 🔢 **Le code d'appairage dans vos notifications** : la [nouvelle doc de référence ESPHome](https://github.com/dasimon135/daikin_madoka/blob/main/docs/esphome-proxy.md) inclut des répondeurs d'appairage qui **poussent le code à 6 chiffres dans Home Assistant** au moment où l'invite s'affiche sur le thermostat. Avec plusieurs Madoka, vous savez enfin *quelle* unité s'appaire via *quel* proxy — il suffit de comparer le code à l'écran.
- 📉 **Fini les trous dans les graphiques** : 1-2 échecs de poll transitoires n'affichent plus les entités en indisponible — les capteurs gardent leur dernière valeur le temps que la connexion se rétablisse. Les vraies pannes, elles, remontent toujours immédiatement.
- 🔍 **Découverte assainie** : les signaux sous −90 dBm ne créent plus de carte de découverte (fini le BRC1H du voisin qui s'invite 😄), et le config flow **teste la connexion — appairage compris — avant** de créer l'entrée : plus jamais d'entrée fantôme bloquée en « nouvel essai de configuration ».
- 🧹 **Nettoyage du registre** : les appareils orphelins laissés par d'anciennes entrées supprimées sont purgés au démarrage, et chaque appareil peut désormais être supprimé individuellement depuis sa page.
- ⚙️ Nécessite **pymadoka-ng 0.3.7** (installé automatiquement).

### 📏 La règle d'or (multi-proxy)

Si vous avez plusieurs proxys actifs à portée : **chaque proxy actif doit être appairé avec chaque thermostat** (l'invite s'affiche une fois par proxy — et maintenant avec le code en notification), sinon passez le proxy en `bluetooth_proxy: active: false`. Tout est détaillé, config YAML complète et tableau de dépannage inclus, dans la [doc ESPHome](https://github.com/dasimon135/daikin_madoka/blob/main/docs/esphome-proxy.md).

### ⬆️ Mise à jour

Via HACS (dépôt personnalisé `dasimon135/daikin_madoka` si ce n'est pas déjà fait) puis redémarrage. Les `entity_id` et l'historique sont conservés. Au premier redémarrage, il est possible qu'une invite d'appairage apparaisse sur un thermostat — c'est le proxy qui régularise son bond : confirmez, et c'est réglé pour de bon.

Retours bienvenus, ici ou sur [GitHub](https://github.com/dasimon135/daikin_madoka/issues) !

Bonne clim' à tous ! ❄️🔥
