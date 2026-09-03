# Annonce HACF — v3.11.0

> À poster dans : https://forum.hacf.fr/t/tuto-controler-un-thermostat-daikin-madoka-brc1h-via-bluetooth-avec-home-assistant-integration-custom-esphome/75688
> En réponse dans le même fil que l'annonce v3.10.0.
> Ne pas coller cet en-tête — le message commence après la ligne ci-dessous.

---

## 🔌 Daikin Madoka v3.11.0

**Les deux nouveautés de cette version ont été écrites par d'autres que moi. C'est une première pour ce projet.**

> ⚠️ **Si tu fais tourner le composant ESPHome sur un ESP32 dédié, tu as une ligne à changer avant de recompiler.** Va voir la section ESPHome plus bas. Si tu utilises l'intégration Home Assistant avec des proxys Bluetooth — c'est le cas de la plupart d'entre vous — il n'y a rien à faire de plus que la mise à jour HACS habituelle.

### Les unités de ventilation sont supportées — VAM et VMC double flux

Merci à **@Frank802**, qui a écrit tout ça, l'a testé sur sa propre unité et a tenu bon pendant une longue relecture.

Jusqu'ici l'intégration partait du principe que derrière un BRC1H il y avait forcément un climatiseur. Une VAM ne fait que ventiler : pas de consigne, pas de chauffage, pas de froid. Ça « marchait », au sens où ça se connectait — et ça t'affichait ensuite une interface de thermostat qui n'avait aucun sens pour elle.

C'est maintenant un type d'appareil à part entière. À l'ajout d'une unité — ou via **Reconfigurer** sur une unité existante — tu choisis **Type d'appareil : Ventilation seule (VAM / HRV)**, et tu obtiens :

- **Arrêt** et **Ventilation seule**, et rien d'autre dans la liste des modes
- deux vitesses, **Basse** et **Haute**
- les trois modes de ventilation en presets : **Auto**, **Échangeur de chaleur**, **Bypass**
- aucune consigne de température, puisqu'il n'y a rien à régler

Côté ESPHome, un composant `madoka_vam` fait la même chose.

**Autant le dire franchement : je n'ai pas de VAM.** Tout ce qui précède a été mesuré par Frank802 sur la sienne. Ce que j'ai vérifié ici, c'est l'autre moitié — qu'un thermostat BRC1H se comporte toujours exactement pareil, parce que cette version déplace aussi du code commun aux deux types d'appareils. Si tu as une VAM et que quelque chose cloche, ouvre une issue : tu me diras une chose que je ne peux pas voir d'ici.

### Capteurs de consommation d'énergie

Merci à **@sharkoz**, qui a découvert que le Madoka tient ses propres compteurs de consommation et a écrit le code pour les lire.

Six capteurs : **aujourd'hui, hier, cette semaine, la semaine dernière, cette année, l'année dernière**. Ils sont **désactivés par défaut** — active *Lire la consommation d'énergie* dans les options de l'intégration. « Énergie aujourd'hui » est celui qui est fait pour le **tableau de bord Énergie** ; les cinq autres sont de simples relevés. Aujourd'hui est rafraîchi toutes les cinq minutes, les autres périodes une fois par jour.

**Toutes les unités n'ont pas ces compteurs.** Les miennes ne les ont pas : mes quatre BRC1H répondent à cette requête par une valeur vide. Ce n'est pas une panne et ce n'est plus traité comme telle — l'intégration le dit une fois dans le journal, coupe l'interrogation énergie pour ce thermostat, et laisse tous les autres relevés tranquilles. Donc si tu actives l'option et que les capteurs restent vides, c'est simplement que ton unité intérieure ne compte pas. Rien d'autre ne casse.

### ESPHome : un seul transport au lieu de deux copies — et une ligne à ajouter

Le composant `madoka_vam` est arrivé en embarquant une copie complète de la couche Bluetooth de `madoka` : sept fonctions identiques caractère pour caractère, quatre autres ne différant que par une étiquette de log. Ça ne pouvait finir que d'une façon — un correctif appliqué dans une copie et pourrissant tranquillement dans l'autre, sans que la CI puisse s'en apercevoir, puisqu'elle compile ces composants sans jamais les exécuter.

La partie commune n'existe désormais qu'une fois, dans un nouveau composant appelé `madoka_base`. 1398 lignes deviennent 1225. L'ordre d'interrogation est inchangé : mêmes commandes, mêmes délais, même séquence, sur les deux composants.

**Ce que tu dois faire.** ESPHome ne copie que les composants externes que tu nommes, et il ne sait pas aller en chercher un qui n'est pas listé. Il faut donc écrire `madoka_base` en toutes lettres :

```yaml
external_components:
  - source:
      type: local
      path: esphome_components
    components: [ madoka, madoka_base ]      # et madoka_vam en plus si tu as une VAM
```

Puis recompiler. Si tu oublies, la compilation s'arrête sur un composant manquant — c'est un échec bruyant, pas silencieux, et il ne peut pas produire un firmware à moitié à jour.

Disparue aussi : la copie de `ble_client` que ce dépôt traînait. Il s'avère qu'elle n'a jamais été compilée — toutes les configs d'ici et de la doc listent `components: [ madoka ]`, donc ESPHome utilisait toujours la sienne. La retirer ne change rien au firmware que tu obtiens. Tout ce pour quoi elle existait est dans ESPHome en standard depuis la 2026.7.2. Le pin ESPHome passe à 2026.8.2 au passage.

### Mise à jour

Via HACS puis redémarrage. Rien à reconfigurer, aucun ré-appairage.

**Si tu utilises le composant ESPHome :** mets à jour les composants, ajoute `madoka_base` à la liste `components:` comme ci-dessus, recompile.

Notes de version : https://github.com/dasimon135/daikin_madoka/releases/tag/v3.11.0
