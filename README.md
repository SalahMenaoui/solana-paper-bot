# solana-paper-bot

Collecteur de données sur les nouveaux memecoins Solana, pour de la **recherche / paper trading uniquement**.
Aucun wallet, aucune clé privée, aucun trade réel, aucune IA. APIs gratuites : DexScreener et RugCheck.

**Phase 1 (ce repo) :** toutes les 30 min, GitHub Actions lance `collect.py`, qui :

1. **suit** les tokens déjà connus : à chaque run pendant 6 h, toutes les ~2 h jusqu'à 24 h, puis aux checkpoints 48h / 72h / 7d (checkpoints officiels : 1h, 6h, 24h, 48h, 72h, 7d, avec une fenêtre de tolérance si le run est en retard) ;
2. **découvre** les nouveaux tokens (profils, boosts et community takeovers DexScreener, nouveaux mints RugCheck) ;
3. prend la **photo t0** : prix, liquidité, volume, achats/ventes, socials, mint/freeze authority, top 10 holders, score RugCheck, gros holders ;
4. **archive** les tokens dont le suivi est fini (voir [Stockage](#stockage)) ;
5. **enregistre** tout sur la branche `data` du repo (SQLite compressé).

## Lancer en local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python pull_data.py      # récupère les données de GitHub -> data/ (à refaire pour mettre à jour)
python stats.py          # statistiques globales
python wallets.py        # classement des wallets (≥ 3 apparitions ; ex. python wallets.py 5)
pip install pytest && python -m pytest   # tests de la logique de suivi
```

`git pull` met à jour le **code** ; `python pull_data.py` met à jour les **données**.

⚠️ Ne lance pas `python collect.py` en local sur `data/` : c'est une copie, ses résultats ne seraient
pas envoyés sur GitHub et seraient écrasés au prochain `pull_data.py`. Pour tester le collecteur :
`BOT_DATA_DIR=/tmp/test python collect.py`.

## Voir les données

- Interface graphique : [DB Browser for SQLite](https://sqlitebrowser.org/) → ouvrir `data/all.db` (tout fusionné).
- En ligne de commande :

```bash
sqlite3 -header -column data/all.db \
  "SELECT symbol, datetime(discovered_at,'unixepoch') AS vu_le, top10_pct, status FROM tokens ORDER BY discovered_at DESC LIMIT 20;"
```

Les temps sont des timestamps Unix UTC : `datetime(colonne, 'unixepoch')` pour les lire.

## Tables

| Table | Contenu |
|---|---|
| `tokens` | infos fixes de chaque token à la découverte + statut (`active` / `rug`) |
| `snapshots` | une ligne par mesure. `checkpoint` = `t0`, `1h`, `6h`, `24h`, `48h`, `72h`, `7d`, ou `NULL` pour une mesure intermédiaire. `measured_at` NULL = checkpoint manqué ; `delay_min` = retard réel |
| `holder_appearances` | gros holders « humains » (hors pools) de chaque token à t0 |
| `candidates` | tokens vus mais pas encore enregistrés (en attente d'une paire DexScreener) |
| `runs` | journal de chaque run (durée, compteurs, erreurs, taille de la base) |

## Stockage

GitHub refuse les fichiers de plus de 100 Mo, et committer une grosse base toutes les 30 min ferait
exploser l'historique Git. Donc ([`bot/storage.py`](bot/storage.py)) :

| Fichier | Contenu |
|---|---|
| `bot.db` | base « live » : seulement les tokens encore suivis (< 8 jours) ; reste petite |
| `archive/bot-AAAA-MM.db` | tokens dont le suivi est fini, par mois de découverte (UTC) ; ne bouge plus une fois le mois fini |
| `all.db` | local uniquement : tout fusionné par `pull_data.py`, pour l'analyse |

Sur GitHub, ces fichiers sont compressés (`.gz`) sur la branche `data`, qui ne contient qu'**un seul commit**,
réécrit à chaque run. Un token passe de la base live à l'archive en une seule transaction : il n'est jamais
perdu ni dupliqué. Les candidats déjà traités sont effacés après 48 h, le journal des runs part à l'archive après 7 jours.

## Régler le bot

Tous les seuils sont dans [`bot/config.py`](bot/config.py) : nb de tokens par run, âge max, règles de rug, fenêtres de tolérance, etc.

## À savoir

- Les profils et boosts DexScreener sont payés par les créateurs : ce n'est pas un échantillon de *tous* les tokens. La colonne `source` permet de comparer les sources.
- Le « meilleur prix » est échantillonné (toutes les 30 min à 2 h) : le vrai pic peut être plus haut.
- RugCheck limite à ~15 requêtes par fenêtre : c'est ce qui plafonne le nombre de nouveaux tokens par run.
