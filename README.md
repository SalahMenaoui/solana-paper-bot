# solana-paper-bot

Collecteur de données sur les nouveaux memecoins Solana, pour de la **recherche / paper trading uniquement**.
Aucun wallet, aucune clé privée, aucun trade réel, aucune IA. APIs gratuites : DexScreener et RugCheck.

**Phase 1 (ce repo) :** toutes les 30 min, GitHub Actions lance `collect.py`, qui :

1. **suit** les tokens déjà connus : à chaque run pendant 6 h, toutes les ~2 h jusqu'à 24 h, puis aux checkpoints 48h / 72h / 7d (checkpoints officiels : 1h, 6h, 24h, 48h, 72h, 7d, avec une fenêtre de tolérance si le run est en retard) ;
2. **découvre** les nouveaux tokens (profils, boosts et community takeovers DexScreener, nouveaux mints RugCheck) ;
3. prend la **photo t0** : prix, liquidité, volume, achats/ventes, socials, mint/freeze authority, top 10 holders, score RugCheck, gros holders ;
4. **enregistre** tout dans `data/bot.db` (SQLite), commité dans le repo.

## Lancer en local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python collect.py        # un run de collecte (~2 min)
python stats.py          # statistiques globales
python wallets.py        # classement des wallets (≥ 3 apparitions ; ex. python wallets.py 5)
pip install pytest && python -m pytest   # tests de la logique de suivi
```

Récupérer les données collectées par GitHub : `git pull`.

## Voir les données

- Interface graphique : [DB Browser for SQLite](https://sqlitebrowser.org/) → ouvrir `data/bot.db`.
- En ligne de commande :

```bash
sqlite3 -header -column data/bot.db \
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

## Régler le bot

Tous les seuils sont dans [`bot/config.py`](bot/config.py) : nb de tokens par run, âge max, règles de rug, fenêtres de tolérance, etc.

## À savoir

- Les profils et boosts DexScreener sont payés par les créateurs : ce n'est pas un échantillon de *tous* les tokens. La colonne `source` permet de comparer les sources.
- Le « meilleur prix » est échantillonné (toutes les 30 min à 2 h) : le vrai pic peut être plus haut.
- RugCheck limite à ~15 requêtes par fenêtre : c'est ce qui plafonne le nombre de nouveaux tokens par run.
