# Annonce HACF — v3.9.0

> Fil : https://forum.hacf.fr/t/tuto-controler-un-thermostat-daikin-madoka-brc1h-via-bluetooth-avec-home-assistant-integration-custom-esphome/75688
> Ne pas coller cet en-tête : le message commence après le trait ci-dessous.

---

## 🔌 Daikin Madoka v3.9.0

La [v3.9.0](https://github.com/dasimon135/daikin_madoka/releases/tag/v3.9.0) corrige **deux façons dont un thermostat en parfait état pouvait être déclaré « appairage requis »** et rester bloqué jusqu'à ce que quelqu'un aille appuyer sur son écran. Les deux ont été mesurées sur du matériel réel, pas déduites.

**1. Le thermostat qui marchait il y a deux minutes.** Chez moi : connexion et remontée de données à 22h11:44, déclaré *appairage requis* à 22h14:00, mort pendant 11 heures. Les refus sont reconnus au texte du message d'erreur, et le BRC1H n'accepte qu'un seul central — donc un lien resté ouvert, ou les quatre thermostats qui se reconnectent ensemble après un redémarrage, produisent le même message qu'un bond réellement perdu. Désormais, un refus qui suit de moins de 10 minutes une session authentifiée réussie est traité comme de la congestion : cadence ralentie, avertissement, mais **aucune quarantaine et personne convoqué devant le thermostat**.

**2. Les proxys enregistrés étaient de la fiction.** Ce n'est pas l'intégration qui choisit le proxy : Home Assistant ne retient que l'adresse du thermostat et re-choisit au signal à *chaque* tentative. Tout ce qui était noté à propos des proxys était donc une intention. Sur un seul redémarrage, mesuré avec une sonde :

| Thermostat | Enregistré | Chemin réel |
|---|---|---|
| 1 | Proxy A | **Proxy B** |
| 2 | Proxy A | **Proxy C** |
| 3 | Proxy C | **Proxy B** |
| 4 | Proxy C | Proxy C ✅ |

**3 sur 4.** C'est ce qui explique les **demandes de réappairage venant de proxys déjà listés comme appairés** : ils n'avaient rien perdu, ils n'avaient simplement jamais porté la session qu'on leur attribuait. Le capteur *Source de connexion*, la liste des proxys appairés et le proxy préféré rapportent maintenant la réalité, et les listes se réparent toutes seules.

### ⚠️ Ce qui reste ouvert

Home Assistant peut toujours router un thermostat vers un proxy avec lequel il n'a jamais été appairé. La v3.9.0 rend ça **visible et inoffensif**, elle ne l'empêche pas. Si un thermostat reste en **« appairage qui n'aboutit pas »**, c'est exactement ce qu'il vous dit : confirmez l'invite sur son écran **une fois pour ce proxy-là**, ou passez le proxy en `bluetooth_proxy: active: false`.

### ⬆️ Mise à jour

Via HACS puis redémarrage. `entity_id` et historique conservés, rien à reconfigurer. Nécessite **pymadoka-ng 0.3.11** (installé automatiquement), donc redémarrage un peu plus long. Ne vous étonnez pas si votre liste de proxys appairés s'allonge sur les premiers redémarrages : c'est l'intégration qui enregistre enfin des chemins qu'elle ne voyait pas.

Retours bienvenus, ici ou sur [GitHub](https://github.com/dasimon135/daikin_madoka/issues) ! ❄️🔥
