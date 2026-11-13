"""
Calcul des "résultats" de chaque token à 24 h, partagé par stats.py et wallets.py
(et réutilisable par le paper trader de la phase 2).
"""
import os
import sqlite3
import sys
import time

from bot import config, db

HOUR = 3600
HORIZON_H = 24       # on évalue la performance à 24 h
TOLERANCE_H = 4      # même tolérance que le checkpoint 24h dans config.py


def open_analysis_db() -> sqlite3.Connection:
    """Ouvre data/all.db (toutes les données fusionnées), construite par pull_data.py."""
    if not os.path.exists(config.ANALYSIS_DB_PATH):
        sys.exit(f"{config.ANALYSIS_DB_PATH} introuvable : lance d'abord  python pull_data.py")
    return db.connect(config.ANALYSIS_DB_PATH)


def token_outcomes(conn, now: int | None = None) -> dict[str, dict]:
    """
    Retourne {adresse: résultat} pour les tokens dont le résultat à 24 h est connu.
    résultat = {
        "final_24h": prix_24h / prix_t0 (0.0 si la paire a disparu, None si checkpoint manqué),
        "peak_24h":  plus haut prix MESURÉ dans les 24 h / prix_t0 (échantillonné, pas le vrai pic),
        "rug_24h":   True si marqué rug dans les 24 h,
        "source", "discovered_at",
    }
    """
    now = now or int(time.time())
    tokens = conn.execute(
        """
        SELECT t.address, t.source, t.discovered_at, t.status, t.rug_at,
               s0.price_usd AS price_t0
        FROM tokens t
        JOIN snapshots s0 ON s0.token_address = t.address AND s0.checkpoint = 't0'
        WHERE s0.price_usd > 0
        """
    ).fetchall()

    outcomes = {}
    for t in tokens:
        end = t["discovered_at"] + (HORIZON_H + TOLERANCE_H) * HOUR
        cp = conn.execute(
            "SELECT measured_at, pair_found, price_usd FROM snapshots WHERE token_address = ? AND checkpoint = '24h'",
            (t["address"],),
        ).fetchone()
        if cp is None and now < end:
            continue  # résultat pas encore connu

        if cp is None or cp["measured_at"] is None:
            final = None                               # checkpoint manqué
        elif not cp["pair_found"] or not cp["price_usd"]:
            final = 0.0                                # paire disparue = perte totale
        else:
            final = cp["price_usd"] / t["price_t0"]

        peak_price = conn.execute(
            "SELECT MAX(price_usd) FROM snapshots WHERE token_address = ? AND measured_at <= ?",
            (t["address"], end),
        ).fetchone()[0]

        outcomes[t["address"]] = {
            "final_24h": final,
            "peak_24h": (peak_price or 0) / t["price_t0"],
            "rug_24h": t["status"] == "rug" and t["rug_at"] - t["discovered_at"] <= HORIZON_H * HOUR,
            "source": t["source"],
            "discovered_at": t["discovered_at"],
        }
    return outcomes
