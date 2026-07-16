# Annonce HACF — v3.0.0 (à poster dans le fil du tuto)

> Fil : https://forum.hacf.fr/t/tuto-controler-un-thermostat-daikin-madoka-brc1h-via-bluetooth-avec-home-assistant-integration-custom-esphome/75688

---

## 🚀 Daikin Madoka v3.0.0 — une carte dédiée, du dépannage guidé, et une reconnexion enfin solide

Bonjour à tous,

La **[v3.0.0](https://github.com/dasimon135/daikin_madoka/releases/tag/v3.0.0)** de l'intégration **Daikin Madoka (BRC1H)** est disponible ! Après la 2.4.0 (« Modern Bluetooth »), cette version se concentre sur l'expérience dashboard et la fiabilité. Le tout validé sur du vrai matériel.

### 🎛️ La Madoka Card

La grosse nouveauté : une **carte Lovelace dédiée**, embarquée **dans l'intégration** (aucune installation séparée, elle s'enregistre toute seule). Elle reprend le cadran du BRC1H — **halo lumineux qui change de couleur selon le mode**, arc de consigne, segments de ventilation, contrôles − ○ +, sélecteur de mode, slider de luminosité, mini-graphe de température (12 h) et badges filtre/signal.

Trois formats au choix :

```yaml
type: custom:madoka-card
entity: climate.mon_madoka
# layout: full | compact | tile
```

- **full** : le cadran complet
- **compact** : cadran + contrôles
- **tile** : une rangée ultra-compacte, alignée avec les tile cards natives de HA (parfait en grille dense)

Elle **découvre toute seule** les entités associées (temp. extérieure, luminosité, filtre, signal) — vous ne donnez que l'entité `climate.*`. Elle suit votre **thème** et votre **langue** (modes traduits via HA, textes de la carte en fr/es/de/it/nl/en).

### 🔧 Dépannage guidé

- Une **réparation « thermostat injoignable »** apparaît (avec lien vers la doc appairage/proxy) quand la connexion échoue durablement, et disparaît toute seule au retour — fini le device « indisponible » sans explication.
- Un **bouton Reconnecter** pour forcer un rétablissement du lien à la demande.

### 🔄 Reconnexion auto-réparante corrigée

Un vrai bug de fond a été attrapé et corrigé : après une coupure BLE, la reconnexion pouvait échouer en `Insufficient authentication` (le bond est stocké **par proxy**). Désormais l'intégration **ré-appaire à chaque reconnexion** → récupération propre. Validé en direct sur mon thermostat.

### ⚡ Et aussi

- **Polling adaptatif** : une commande se reflète immédiatement dans l'UI (plus besoin d'attendre le cycle de poll).
- Capteur **Temps de fonctionnement** (heures d'allumage cumulées, persistant).

### ⚠️ Rappel proxy Bluetooth

Inchangé depuis la 2.4.0 : le BRC1H exige un **appairage authentifié**. Le firmware bluetooth-proxy standard ne sait pas le faire — quelques lignes à ajouter au YAML du proxy, tout est dans la [section Requirements du README](https://github.com/dasimon135/daikin_madoka#requirements).

### ⬆️ Mise à jour

Mise à jour HACS + redémarrage. Les `entity_id` et l'historique sont conservés ; la lib passe à **pymadoka-ng 0.3.5** (installée automatiquement). Un **rechargement forcé du navigateur** (Ctrl+Shift+R) pour voir la carte.

*(Pas encore dans le store HACS par défaut — ajoutez `https://github.com/dasimon135/daikin_madoka` en dépôt custom, catégorie Intégration, en attendant.)*

Retours bienvenus, ici ou sur [GitHub](https://github.com/dasimon135/daikin_madoka/issues) ! ❄️🔥
