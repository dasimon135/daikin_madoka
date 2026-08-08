# Annonce HACF — v3.9.0 (à poster dans le fil du tuto)

> Fil : https://forum.hacf.fr/t/tuto-controler-un-thermostat-daikin-madoka-brc1h-via-bluetooth-avec-home-assistant-integration-custom-esphome/75688

---

## 🔌 Daikin Madoka v3.9.0 — le proxy affiché est enfin celui qui porte vraiment la connexion

Bonjour à tous,

La [v3.9.0](https://github.com/dasimon135/daikin_madoka/releases/tag/v3.9.0) corrige **deux façons dont un thermostat en parfait état pouvait être déclaré « appairage requis »** et se retrouver bloqué jusqu'à ce que quelqu'un se déplace physiquement devant lui.

La différence avec les releases précédentes : cette fois, rien n'a été déduit. Les deux bugs ont été **mesurés sur du matériel réel**, et le correctif a été vérifié de la même façon. 🔬

### 🧊 Bug n°1 — le thermostat qui fonctionnait il y a deux minutes

Chez moi, le 7 août à 22h10 : redémarrage de Home Assistant. À **22h11:44**, le Salon se connecte et remonte ses données — donc bond valide, session authentifiée, tout va bien. À **22h14:00**, il est déclaré *appairage requis*, les reconnexions automatiques s'arrêtent, et il reste mort **11 heures** jusqu'à ce que j'aille appuyer sur son écran. 😤

Or un thermostat qui s'authentifie à 22h11:44 n'a pas perdu son bond sur deux proxys à 22h14:00.

L'explication : les refus sont reconnus **au texte du message d'erreur** (`insufficient authentication`, ATT `error=5`). Et le BRC1H n'accepte qu'un seul central à la fois — donc un lien resté ouvert côté proxy, ou simplement les quatre thermostats qui se reconnectent tous en même temps après un redémarrage, produisent **exactement le même message** qu'un bond réellement perdu.

**Désormais** : un refus qui arrive dans les 10 minutes suivant une session authentifiée réussie est traité comme de la congestion. La cadence passe à une tentative par quart d'heure, un avertissement s'affiche, mais **rien n'est mis en quarantaine et personne n'est convoqué devant le thermostat**. Une bonne session excuse un refus — un bond réellement mort ressort au tour suivant.

### 🎭 Bug n°2 — les proxys enregistrés étaient de la fiction

Celui-là est plus profond, et il explique probablement pas mal de choses chez vous aussi.

**Ce n'est pas l'intégration qui choisit le proxy.** Home Assistant ne retient que l'adresse du thermostat et re-choisit un proxy au signal, à *chaque* tentative. Tout ce que l'intégration notait à propos des proxys était donc une **intention**, jamais une observation.

J'ai posé une sonde temporaire pour lire ce que la vraie pile Bluetooth contient. Sur un seul redémarrage, **3 thermostats sur 4** étaient servis par un proxy différent de celui enregistré :

| Thermostat | Enregistré | Chemin réel |
|---|---|---|
| Parents | Proxy Parents | **Proxy Buanderie** |
| Valentine | Proxy Parents | **Proxy Valentine** |
| Salon | Proxy Valentine | **Proxy Buanderie** |
| Manon | Proxy Valentine | Proxy Valentine ✅ |

C'est ça qui explique **les demandes de réappairage répétées venant de proxys déjà listés comme appairés** : ils n'avaient rien perdu du tout, ils n'avaient tout simplement jamais porté la session que l'enregistrement leur attribuait. Et un refus pouvait être imputé — voire coûter son bond — à un proxy qui n'avait pas participé au tour.

**Désormais** : le capteur **Source de connexion**, la liste des proxys appairés et le proxy préféré rapportent tous ce qui s'est réellement passé. Les listes **se réparent toutes seules** au fil des connexions observées. Et un échec d'appairage n'est imputé à un proxy que si la tentative peut réellement le nommer — sinon personne n'est accusé, au lieu de deviner.

### ⚠️ Ce qui reste ouvert — à savoir

Home Assistant peut toujours router un thermostat via un proxy avec lequel il n'a jamais été appairé. La v3.9.0 rend ça **visible et inoffensif** (la connexion est rapportée honnêtement, aucun proxy n'est accusé à tort), mais **ne l'empêche pas**.

Concrètement : si un thermostat reste en **« appairage qui n'aboutit pas »**, c'est exactement ce qu'il vous dit. Le remède est inchangé — confirmez l'invite d'appairage sur son écran **une fois pour ce proxy-là**, ou passez le proxy en `bluetooth_proxy: active: false`.

Un exemple vu en direct juste après le déploiement, sur mon thermostat Parents :

```
did not complete pairing ... (tried via: D0:CF:13:0F:11:F6, D0:CF:13:0F:11:F6)
```

**Le même proxy deux fois dans le même tour.** Avant la v3.9.0, ce log aurait affiché deux adresses différentes et donné l'illusion que deux chemins avaient été essayés. C'est la démonstration directe qu'itérer plusieurs candidats ne teste pas plusieurs chemins : Home Assistant renvoie le même gagnant à chaque fois.

### ⬆️ Mise à jour

Via HACS (dépôt personnalisé `dasimon135/daikin_madoka` si ce n'est pas déjà fait) puis redémarrage. Les `entity_id` et l'historique sont conservés, rien à reconfigurer. Nécessite **pymadoka-ng 0.3.11**, installé automatiquement — le redémarrage sera donc un peu plus long.

Ne vous étonnez pas si votre liste de proxys appairés **s'allonge** sur les premiers redémarrages : c'est l'intégration qui enregistre enfin des chemins qu'elle ne voyait pas.

Retours bienvenus, ici ou sur [GitHub](https://github.com/dasimon135/daikin_madoka/issues) !

Bonne clim' à tous ! ❄️🔥
