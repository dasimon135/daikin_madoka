# Annonce HACF — v3.9.2

> À poster dans : https://forum.hacf.fr/t/tuto-controler-un-thermostat-daikin-madoka-brc1h-via-bluetooth-avec-home-assistant-integration-custom-esphome/75688
> Ne pas coller cet en-tête — le message commence après la ligne ci-dessous.

---

## 🔌 Daikin Madoka v3.9.2

**Cette version corrige quelque chose que la v3.9.1 avait cassé.** Si tu as mis à jour il y a deux jours et que ta consigne atterrit un degré trop haut, c'est de ça qu'il s'agit.

La v3.9.1 a fait fonctionner l'écriture des consignes sur les unités en consigne unique en portant la cible dans les deux registres de la trame. C'est correct pour un thermostat qui annonce un différentiel minimum de zéro : il enregistre la paire identique telle quelle. C'est le cas des unités de @mauriziofanetti-hue, et des miennes.

Certains BRC1H annoncent un **différentiel minimum non nul** : ils gardent au moins un degré entre la consigne froid et la consigne chaud, et ne peuvent pas tenir une paire identique. La trame porte le froid avant le chaud, donc l'application du chaud casse l'écart et le thermostat le rétablit en poussant vers le haut le registre qu'il vient d'appliquer — le froid. Tu demandes 26, tu obtiens 27. Tu demandes un degré de moins, rien ne bouge en apparence, parce que la correction le remet aussitôt.

La paire écrite porte désormais le différentiel minimum annoncé par le thermostat au lieu de supposer zéro. Sur une unité qui annonce zéro, la trame est identique octet pour octet à celle de la v3.9.1 : rien ne change pour elles.

@speynaud a trouvé le problème, fourni les diagnostics et les traces qui ont désigné le champ en cause, puis validé le correctif sur le seul matériel qui le reproduit — aucun de mes thermostats n'annonce un différentiel non nul, je n'aurais donc pu ni le détecter ni le vérifier seul. Sa validation porte sur le mode Froid ; le chemin Chaud suit la même règle mais n'a pas été mesuré.

Mise à jour via HACS puis redémarrage, rien à reconfigurer. **Si tu utilises le composant ESPHome**, il lit et applique désormais le même différentiel : recompile avec `ref: v3.9.2`.

Notes de version : https://github.com/dasimon135/daikin_madoka/releases/tag/v3.9.2
