"""
Accès à l'API publique DexScreener (gratuite, sans clé).
Doc : https://docs.dexscreener.com/api/reference
"""
from bot import config
from bot.http_client import get_json

BASE = config.DEXSCREENER_BASE


# --- Découverte ----------------------------------------------------------------

def _solana_addresses(items) -> list[str]:
    """Garde uniquement les adresses de tokens Solana d'une liste de profils/boosts."""
    return [
        item["tokenAddress"]
        for item in (items or [])
        if item.get("chainId") == "solana" and item.get("tokenAddress")
    ]


def latest_profiles() -> list[str]:
    """Tokens qui viennent de créer un profil DexScreener (payant pour le créateur)."""
    return _solana_addresses(get_json(f"{BASE}/token-profiles/latest/v1", config.DEX_SLOW_INTERVAL_S))


def latest_boosts() -> list[str]:
    """Tokens qui viennent d'acheter un boost (payant)."""
    return _solana_addresses(get_json(f"{BASE}/token-boosts/latest/v1", config.DEX_SLOW_INTERVAL_S))


def latest_takeovers() -> list[str]:
    """Community takeovers récents (la communauté reprend un token abandonné)."""
    return _solana_addresses(get_json(f"{BASE}/community-takeovers/latest/v1", config.DEX_SLOW_INTERVAL_S))


# --- Données de marché ---------------------------------------------------------

def _liquidity(pair: dict) -> float:
    return ((pair.get("liquidity") or {}).get("usd")) or 0.0


def best_pairs(addresses: list[str]) -> dict[str, dict]:
    """
    Pour une liste d'au plus 30 adresses de tokens, retourne {adresse: paire}
    en gardant, pour chaque token, la paire la plus liquide (celle où on tradérait).
    Un token absent du dictionnaire = aucune paire trouvée.
    """
    assert len(addresses) <= config.DEX_BATCH_SIZE
    url = f"{BASE}/tokens/v1/solana/{','.join(addresses)}"
    pairs = get_json(url, config.DEX_FAST_INTERVAL_S) or []

    wanted = set(addresses)
    best: dict[str, dict] = {}
    for pair in pairs:
        token = (pair.get("baseToken") or {}).get("address")
        if token not in wanted:
            continue  # le token est en "quote" dans cette paire, on l'ignore
        if token not in best or _liquidity(pair) > _liquidity(best[token]):
            best[token] = pair
    return best


def pair_metrics(pair: dict) -> dict:
    """Extrait les chiffres de marché d'une paire (ce qu'on stocke dans snapshots)."""
    txns = pair.get("txns") or {}
    volume = pair.get("volume") or {}

    def as_float(value):
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "pair_address": pair.get("pairAddress"),
        "price_usd": as_float(pair.get("priceUsd")),
        "market_cap": as_float(pair.get("marketCap")),
        "fdv": as_float(pair.get("fdv")),
        "liquidity_usd": as_float((pair.get("liquidity") or {}).get("usd")),
        "volume_m5": as_float(volume.get("m5")),
        "volume_h1": as_float(volume.get("h1")),
        "volume_h24": as_float(volume.get("h24")),
        "buys_m5": (txns.get("m5") or {}).get("buys"),
        "sells_m5": (txns.get("m5") or {}).get("sells"),
        "buys_h1": (txns.get("h1") or {}).get("buys"),
        "sells_h1": (txns.get("h1") or {}).get("sells"),
    }


def socials(pair: dict) -> dict:
    """Présence d'un site, d'un compte X/Twitter et d'un Telegram."""
    info = pair.get("info") or {}
    kinds = set()
    for social in info.get("socials") or []:
        # L'API a utilisé "type" puis "platform" selon les versions : on gère les deux.
        kinds.add((social.get("type") or social.get("platform") or "").lower())
    return {
        "has_website": int(bool(info.get("websites"))),
        "has_twitter": int(bool(kinds & {"twitter", "x"})),
        "has_telegram": int("telegram" in kinds),
    }
