"""Tests du calcul de liquidité verrouillée. Lancer avec : python -m pytest"""
from bot.rugcheck import lp_lock


def market(pct, quote, base):
    return {"lp": {"lpLockedPct": pct, "quoteUSD": quote, "baseUSD": base}}


def test_pondere_par_les_dollars():
    # 9 000 $ verrouillés à 100 % + 1 000 $ pas verrouillés -> 90 %
    r = lp_lock([market(100, 4500, 4500), market(0, 500, 500)])
    assert r == {"lp_locked_pct": 90.0, "lp_locked_usd": 9000.0, "rc_liquidity_usd": 10000.0}


def test_sans_pool():
    assert lp_lock([]) == {"lp_locked_pct": None, "lp_locked_usd": None, "rc_liquidity_usd": None}


def test_pools_vides_et_valeurs_manquantes():
    r = lp_lock([{"lp": None}, market(None, 0, 0)])
    assert r["lp_locked_pct"] is None and r["rc_liquidity_usd"] == 0
