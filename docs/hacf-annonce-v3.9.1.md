# Annonce HACF — v3.9.1

> À poster dans : https://forum.hacf.fr/t/tuto-controler-un-thermostat-daikin-madoka-brc1h-via-bluetooth-avec-home-assistant-integration-custom-esphome/75688
> Ne pas coller cet en-tête — le message commence après la ligne ci-dessous.

---

## 🔌 Daikin Madoka v3.9.1

Deux correctifs, tous deux venus de retours utilisateurs.

**Les consignes n'étaient pas appliquées sur les unités en consigne unique.** Si ton BRC1H est réglé en *Logique de consigne → Consigne unique* (mode plage désactivé), changer la température depuis HA ne faisait rien : le thermostat gardait l'ancienne valeur, sans erreur, sans trace. L'intégration écrivait la nouvelle consigne dans le registre froid et laissait l'ancienne dans le registre chaud — une paire que le BRC1H juge incohérente avec sa propre configuration, et qu'il rejette en silence. Exactement le même mode de panne que les bornes à zéro corrigées en v2.4.0, un champ plus loin. Le mode AUTO n'était pas touché, ce qui explique que ça ne se voyait qu'en Froid et en Chaud.

Les deux consignes portent désormais la même valeur quand le mode plage est désactivé. Validé ici sur matériel avant publication : la trame sortante contient bien la même valeur dans les deux registres.

Petite ironie : **mes propres thermostats sont dans ce cas**, je vivais donc le bug sans l'avoir jamais remarqué. Merci à @mauriziofanetti-hue, qui l'a diagnostiqué avec un relevé de trame BLE pointant la ligne fautive, et à @aureliofrohlich pour la confirmation.

**Plantage au démarrage avec certaines autres intégrations.** Le ménage des appareils orphelins supposait que tout identifiant d'appareil de Home Assistant contenait exactement deux éléments. `rfxtrx` en utilise quatre — et comme ce ménage tourne à chaque chargement, un seul appareil de ce type suffisait à empêcher l'intégration de démarrer. Signalé par un utilisateur sur GitHub.

Mise à jour via HACS puis redémarrage, rien à reconfigurer. **Si tu utilises le composant ESPHome et non l'intégration**, il portait le même bug de consigne : il faut recompiler avec `ref: v3.9.1`.

Notes de version : https://github.com/dasimon135/daikin_madoka/releases/tag/v3.9.1
