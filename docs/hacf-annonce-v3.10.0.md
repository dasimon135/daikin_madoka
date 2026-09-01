# Annonce HACF — v3.10.0

> À poster dans : https://forum.hacf.fr/t/tuto-controler-un-thermostat-daikin-madoka-brc1h-via-bluetooth-avec-home-assistant-integration-custom-esphome/75688
> En réponse dans le même fil que l'annonce v3.9.2.
> Ne pas coller cet en-tête — le message commence après la ligne ci-dessous.

---

## 🔌 Daikin Madoka v3.10.0

**Si ton thermostat affiche un code à 6 chiffres tout seul, sans que personne ne lui ait rien demandé : c'est le sujet de cette version.**

### Ce qui se passait

Home Assistant choisit lui-même par quel chemin Bluetooth il se reconnecte, et il refait ce choix à chaque tentative. Il pouvait donc très bien passer par un proxy qui n'a jamais été appairé avec ce thermostat. Dans ce cas l'appairage démarre, et le thermostat affiche un code à 6 chiffres en attendant qu'un humain le confirme. À 3 h du matin, personne ne le confirme.

Rien n'était visible dans Home Assistant : l'entité restait disponible, parce que la tentative suivante passait par un bon chemin. Le seul témoin, c'était l'écran allumé dans le salon.

Mesuré chez moi : **un thermostat a affiché huit codes en une heure**, tous par des proxys qu'il n'aurait jamais dû utiliser.

Un détail contre-intuitif au passage : ce n'est pas « le proxy le plus proche gagne ». Dans le cas mesuré, le proxy élu était **15 dB plus faible** qu'un proxy correctement appairé disponible — il a gagné parce que Home Assistant compte aussi les slots libres et les échecs récents. N'importe quel proxy à portée peut être choisi à n'importe quel moment.

### Ce qui change

La vérification a été déplacée là où elle mord vraiment : **une fois la connexion établie, juste avant l'appairage**, et sur le chemin réellement utilisé — plus sur celui qu'on avait proposé. Si ce chemin n'est pas sanctionné, la connexion est refermée sans aucun échange d'appairage. Le thermostat n'affiche rien.

Un proxy qui a perdu ses clés est aussi retiré de la liste des chemins de confiance, au lieu d'être re-tenté indéfiniment.

### Une nouvelle réparation, pour le seul cas qu'un humain peut régler

Si Home Assistant s'obstine à choisir un chemin non sanctionné trois fois de suite, tu verras apparaître une **réparation** dans Home Assistant. Elle nomme le proxy sur lequel la connexion retombe, et propose le flux de ré-appairage.

Autrement dit : au lieu de harceler le thermostat, l'intégration te dit avec quel proxy aller t'appairer.

C'est un avertissement, pas une erreur. Aucun appairage n'a été refusé — il n'y en a simplement pas eu.

### Ce que ça coûte, dit franchement

Il faut être clair là-dessus, parce que c'est un vrai coût et je l'ai vécu.

Si Home Assistant choisit un chemin non sanctionné à **toutes** les tentatives d'un cycle, l'intégration les refuse toutes, et le thermostat passe **indisponible**.

C'est le compromis assumé : un thermostat indisponible, avec une réparation qui te dit quoi faire, vaut mieux qu'un thermostat qui marche en allumant chaque nuit un code que personne ne confirme.

Mesuré le 29/08 chez moi : indisponible à partir de 16 h 29, revenu tout seul à 16 h 52 quand le classement des proxys a bougé — sans appairage, sans code affiché, sans que je touche à quoi que ce soit. Ça dure donc de quelques minutes à quelques heures, et **le bouton Reconnecter y met fin quand tu veux**.

### Deux correctifs qui n'ont rien à voir mais que vous avez signalés ici

**La carte qui disparaît.** Le fameux `custom element doesn't exist: madoka-card`, sans logique apparente — merci @Quev1n de l'avoir remonté. Le fichier de la carte n'était servi qu'une fois les thermostats interrogés, plusieurs secondes après le démarrage de Home Assistant. Ouvrir un tableau de bord juste après un redémarrage tombait pile dans ce trou : le navigateur recevait une page d'erreur à la place du JavaScript, et la carte restait cassée jusqu'à un rechargement manuel. La carte est maintenant servie dès le chargement du composant, avant tout le reste.

**La page de l'appareil enfin remplie.** Le modèle et la version n'y arrivaient jamais, sur toutes les installations : ils étaient lus avant même que la connexion Bluetooth existe. Ils sont maintenant lus après un relevé réussi.

⚠️ Avec une surprise à la lecture sur du vrai matériel : ce que le BRC1H publie là décrit **son module radio** (un « UE878 RF MODULE » d'Universal Electronics), pas le thermostat Daikin. Donc le « modèle 0.1 » que tu vas voir est celui de la radio. La version de firmware Daikin, elle, n'est pas exposée en Bluetooth du tout.

### Mise à jour

Via HACS puis redémarrage. Rien à reconfigurer, aucun ré-appairage à faire.

**Si tu utilises le composant ESPHome : rien à faire cette fois.** Contrairement à la v3.9.2, cette version ne touche pas au composant, inutile de recompiler.

Notes de version : https://github.com/dasimon135/daikin_madoka/releases/tag/v3.10.0
