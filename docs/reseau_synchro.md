# Réseau, synchronisation Google Drive et services

[← README](../README.md) · Spécification : §5.3 (réseau), §5.4 (Drive), §7.1 (supervision)

## Bascule WiFi / point d'accès

`scripts/wifi_or_ap.sh`, exécuté toutes les ~30 s par `systemd/wifi-or-ap.timer` :

- **Déjà connecté à un vrai WiFi** : contrôle de qualité (signal + ping de la passerelle). Sain, rien ne change. En échec, un compteur persiste (`WIFI_HEALTH_FILE`) ; après `WIFI_FAIL_THRESHOLD` échecs consécutifs (défaut 3), le réseau est **mis à l'écart** `WIFI_BLACKLIST_MIN` minutes (défaut 10), la connexion coupée, et la recherche d'un autre réseau reprend aussitôt. C'est ce qui évite le ping-pong WiFi ↔ AP.
- **Sinon** : les profils connus dont le réseau est visible, non écarté et au-dessus de `WIFI_SIGNAL_MIN` (défaut 25 %) sont tentés par signal décroissant (`CONNECT_TIMEOUT_SEC` chacun). Le point d'accès n'est coupé qu'au début d'une tentative réelle, pour ne jamais laisser le Pi injoignable.
- **Si rien ne fonctionne** : repli sur le point d'accès `AP_CONNECTION_NAME` (créé au besoin), SSID `AP_SSID`, IP fixe `AP_IP`.
- Chaque décision est journalisée dans `logs/reseau.log` (rotation 5 × 1 Mo).

```bash
./scripts/wifi_or_ap.sh   # passage manuel (NetworkManager requis)
```

⚠️ Ce script touche à la connectivité du Pi lui-même : à essayer d'abord avec un accès console de secours (checklist §7.6, points 7 et 7bis). Pour connecter le Pi au WiFi du lieu, voir la page [`/wifi` du dashboard](dashboard.md#qr-codes-et-wifi).

## Synchronisation Google Drive

`src/rclone_sync.py` exécute **deux jambes par cycle**, aux règles volontairement différentes :

| Dossier | Sens | Commande | Pourquoi |
|---|---|---|---|
| `messages/` | montant seul | `rclone copy` (**jamais `sync`**) | Les enregistrements des invités sont le livrable : rien côté Drive ne doit pouvoir les effacer. |
| `audio_src/` | **dans les deux sens** | `rclone bisync` | Déposer une sonnerie ou un message des mariés depuis un smartphone ; c'est aussi par là que passe le tri des messages fait sur un PC. |

Les deux dossiers Drive doivent être **distincts et non imbriqués** ; le dashboard refuse toute autre configuration, sinon les enregistrements deviendraient de fait bidirectionnels. Les jambes passent en séquence, `messages/` d'abord.

```bash
rclone config                            # remote Google Drive, une fois avant l'événement
sudo ./scripts/setup_rclone_systemd.sh   # installation unique : unités systemd + règle sudoers ciblée
python3 src/rclone_sync.py --run         # un cycle complet (messages + sources audio)
python3 src/rclone_sync.py --resync      # (ré)initialise la synchro bidirectionnelle de audio_src/
```

- **Configuration** : `rclone_config.json` (`remote`, `dossier`, `intervalle_min`, `actif`, `sources_dossier`, `sources_actif`, `sources_resync_fait`), éditable depuis `/rclone`. La synchro bidirectionnelle reste inactive tant qu'elle n'y est pas activée.
- **Échecs tolérés** : pas d'internet, remote indisponible… sont journalisés dans `logs/rclone.log` ; le cycle suivant retente.
- **Premier passage (`--resync`)** : `bisync` a besoin d'un état de référence. Il est lancé automatiquement au premier cycle si rclone est assez récent (≥ 1.66) pour `--resync-mode newer` ; sinon il prendrait le Pi comme référence et supprimerait du Drive ce qui n'est pas encore descendu, et l'initialisation attend le bouton dédié de `/rclone`. Si rclone perd son état, un `--resync` est retenté une fois. Un verrou empêche le timer et le bouton « Synchroniser maintenant » de se marcher dessus.
- **Conflits** : si un fichier est modifié des deux côtés entre deux synchros, le plus récent gagne et l'autre est gardé sous un nom suffixé.

Page **`/rclone`** : statut de chaque jambe (dernière synchro réussie, fichiers en attente calculés sans rien modifier, erreurs récentes, état du bisync), « Synchroniser maintenant », « Réinitialiser la synchronisation bidirectionnelle » (administrateur, avec confirmation : à faire à l'installation, pas pendant l'événement) et formulaire de configuration. Changer l'intervalle régénère `systemd/rclone-sync.timer` et recharge le service automatiquement.

`setup_rclone_systemd.sh` installe une **règle sudoers strictement ciblée** : sans mot de passe, uniquement `systemctl daemon-reload`, `restart rclone-sync.timer` et `start rclone-sync.timer` (§5.4).

## Services systemd et watchdog

```bash
sudo ./scripts/setup_systemd.sh          # livre-dor, dashboard, watchdog, wifi-or-ap
sudo ./scripts/setup_rclone_systemd.sh   # synchronisation Google Drive (règle sudoers dédiée)
```

- **`livre-dor.service`** et **`dashboard.service`** : `Restart=always`, `RestartSec=5`, `StartLimitIntervalSec=120` / `StartLimitBurst=6`. `livre-dor.service` n'a **aucune dépendance réseau** : il enregistre même sans réseau (§7.1).
- **`src/watchdog.py`**, toutes les 2 min (`livre-dor-watchdog.timer`), n'abandonne jamais :
  1. un service (`livre-dor`, `dashboard`, `wifi-or-ap`, `rclone-sync`) en état `failed` est relancé (`reset-failed` puis `restart`) à chaque passage ;
  2. si `status.json` n'a pas bougé depuis `WATCHDOG_STALE_AFTER_SEC` (défaut 300 s), `livre-dor.service` est redémarré : processus gelé plutôt que planté.
- Logs : `journalctl -u <unité> -f`, en plus des fichiers de `logs/`.
