"""
Accès à l'API publique RugCheck (endpoints GET sans clé).
Doc : https://api.rugcheck.xyz/swagger/index.html
Limite observée : header x-rate-limit-limit = 15 -> on espace les appels de ~4 s.
"""
import json

from bot import config
from bot.http_client import get_json

BASE = config.RUGCHECK_BASE


def new_tokens() -> list[str]:
    """Les ~10 derniers tokens créés détectés par RugCheck (souvent âgés de quelques secondes)."""
    items = get_json(f"{BASE}/v1/stats/new_tokens", config.RUGCHECK_INTERVAL_S, max_retries=1)
    return [item["mint"] for item in (items or []) if item.get("mint")]


def report(mint: str) -> dict:
    """
    Récupère le rapport complet d'un token et n'en garde que l'essentiel.
    (Le /report/summary ne contient pas mint/freeze authority ni les holders.)
    """
    data = get_json(f"{BASE}/v1/tokens/{mint}/report", config.RUGCHECK_INTERVAL_S, max_retries=2)
    if not data:
        raise ValueError("rapport RugCheck vide")

    # knownAccounts = adresses identifiées par RugCheck (pools AMM, bonding curve,
    # lockers, créateur...). On les exclut des holders sauf le créateur.
    known = data.get("knownAccounts") or {}
    creator = data.get("creator")

    holders = []
    for holder in data.get("topHolders") or []:
        wallet = holder.get("owner") or holder.get("address")
        info = known.get(wallet) or known.get(holder.get("address")) or {}
        kind = (info.get("type") or "").upper()
        if info and kind != "CREATOR":
            continue  # pool, bonding curve, locker... ce n'est pas un trader
        holders.append({
            "wallet": wallet,
            "pct": holder.get("pct") or 0.0,
            "is_insider": int(bool(holder.get("insider"))),
            "is_creator": int(wallet == creator or kind == "CREATOR"),
        })
    holders = holders[: config.TOP_HOLDERS_TO_STORE]

    return {
        "mint_authority_enabled": int(bool(data.get("mintAuthority"))),
        "freeze_authority_enabled": int(bool(data.get("freezeAuthority"))),
        "top10_pct": round(sum(h["pct"] for h in holders[:10]), 2),
        "rc_score": data.get("score"),
        "rc_score_norm": data.get("score_normalised"),
        "rc_rugged": int(bool(data.get("rugged"))),
        "rc_risks": json.dumps([r.get("name") for r in data.get("risks") or []], ensure_ascii=False),
        "launchpad": (data.get("launchpad") or {}).get("platform"),
        "creator": creator,
        "total_holders": data.get("totalHolders"),
        "holders": holders,
    }
