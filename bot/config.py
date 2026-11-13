"""
Tous les réglages du bot au même endroit.
Modifie les valeurs ici plutôt que dans le code.
Les durées sont en secondes sauf si le nom finit par _H (heures) ou _MIN (minutes).
"""

import os

# --- Fichiers -----------------------------------------------------------------
# Organisation (voir README, section "Stockage") :
#   data/bot.db                   base "live" : seulement les tokens encore suivis (< 8 jours)
#   data/archive/bot-AAAA-MM.db   tokens dont le suivi est fini, rangés par mois de découverte
#   data/all.db                   copie fusionnée de tout (locale, pour stats.py / wallets.py)
# Sur GitHub, ces fichiers sont stockés compressés (.gz) sur la branche "data".
# Variables d'environnement : BOT_DATA_DIR = autre dossier, BOT_DB_PATH = autre base live (tests).
DATA_DIR = os.environ.get("BOT_DATA_DIR", "data")
DB_PATH = os.environ.get("BOT_DB_PATH", os.path.join(DATA_DIR, "bot.db"))
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
ANALYSIS_DB_PATH = os.path.join(DATA_DIR, "all.db")

# --- Budget d'un run (GitHub Actions doit finir en < 3 min) --------------------
# Le setup d'Actions prend ~30 s, donc le script s'arrête de lui-même après ça.
RUN_TIME_BUDGET_S = 140

# --- APIs ----------------------------------------------------------------------
DEXSCREENER_BASE = "https://api.dexscreener.com"
RUGCHECK_BASE = "https://api.rugcheck.xyz"
USER_AGENT = "solana-paper-bot/0.1 (paper trading research)"

HTTP_TIMEOUT_S = 20
MAX_RETRIES = 3          # nb de nouvelles tentatives après un échec (429, 5xx, réseau)
BACKOFF_BASE_S = 2       # attente = 2, 4, 8 s... (doublée à chaque essai)
BACKOFF_MAX_S = 30       # jamais plus de 30 s d'attente d'un coup

# Pause minimale entre deux appels au même service.
# DexScreener : 60 req/min pour profils/boosts, ~300 req/min pour les paires.
DEX_SLOW_INTERVAL_S = 1.1    # endpoints de découverte (60/min)
DEX_FAST_INTERVAL_S = 0.25   # endpoint /tokens/v1 (300/min)
DEX_BATCH_SIZE = 30          # nb max d'adresses par appel /tokens/v1
# RugCheck : header x-rate-limit-limit = 15 (on suppose 15/min) -> 1 appel / 4,2 s
RUGCHECK_INTERVAL_S = 4.2

# --- Découverte ----------------------------------------------------------------
MAX_NEW_TOKENS_PER_RUN = 25   # limité surtout par RugCheck (~4 s par token)
MAX_PAIR_AGE_H = 24           # on ignore les paires plus vieilles que ça
CANDIDATE_EXPIRY_H = 3        # un candidat sans paire / non traité après 3 h est abandonné

# --- Suivi ---------------------------------------------------------------------
# Checkpoints "officiels" : (étiquette, heures après la découverte, tolérance en heures).
# Si le run est en retard, la mesure est acceptée jusqu'à cible + tolérance,
# avec l'heure réelle enregistrée. Au-delà, le checkpoint est marqué "manqué".
CHECKPOINTS = [
    ("1h", 1, 1),
    ("6h", 6, 2),
    ("24h", 24, 4),
    ("48h", 48, 8),
    ("72h", 72, 8),
    ("7d", 168, 24),
]

# Mesures intermédiaires (pour avoir la courbe de prix complète) :
#   - pendant les 6 premières heures : à chaque run (écart min 20 min)
#   - de 6 h à 24 h : environ toutes les 2 h
#   - après 24 h : seulement aux checkpoints officiels
# Les tokens "rug" ne sont mesurés qu'aux checkpoints officiels (économise de la place).
DENSE_FAST_UNTIL_H = 6
DENSE_FAST_GAP_MIN = 20
DENSE_SLOW_UNTIL_H = 24
DENSE_SLOW_GAP_MIN = 110

# --- Détection de rug ----------------------------------------------------------
# Un token est marqué "rug" si :
#   - sa liquidité tombe sous 10 % de sa liquidité à t0, OU
#   - sa liquidité tombe sous 1 000 $ alors qu'elle valait au moins 5 000 $ à t0, OU
#   - sa paire est introuvable 2 mesures de suite, OU
#   - son prix tombe sous 5 % de t0 ET le volume 1 h est < 100 $ (token mort,
#     utile pour les tokens pump.fun encore sur la bonding curve, sans liquidité affichée), OU
#   - RugCheck le signale déjà comme "rugged" à la découverte.
# Le token continue d'être mesuré aux checkpoints officiels après le marquage.
RUG_LIQ_FRACTION = 0.10
RUG_LIQ_ABS_USD = 1000
RUG_LIQ_ABS_APPLIES_FROM_USD = 5000
RUG_MISSING_IN_A_ROW = 2
DEAD_PRICE_FRACTION = 0.05
DEAD_VOLUME_H1_USD = 100

# --- Stockage -----------------------------------------------------------------
# Les candidats déjà traités (promus / trop vieux / expirés) sont effacés après ce délai.
CANDIDATE_RETENTION_H = 48
# Les lignes du journal des runs restent dans la base live ce nombre de jours,
# puis partent dans l'archive du mois.
RUNS_RETENTION_DAYS = 7

# --- Holders (analyse des wallets) ----------------------------------------------
TOP_HOLDERS_TO_STORE = 20   # on garde les 20 plus gros holders "humains" (hors pools)
