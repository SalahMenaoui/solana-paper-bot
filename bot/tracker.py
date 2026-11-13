"""
Logique de suivi, en fonctions "pures" (pas d'API, pas de base) -> faciles à tester.
Tous les temps sont des timestamps Unix en secondes (UTC).
"""
from bot import config

HOUR = 3600
MINUTE = 60


def due_checkpoints(discovered_at: int, now: int, done_labels: set[str]):
    """
    Retourne (à_mesurer, manqués) : deux listes de (étiquette, heure_cible).
      - à_mesurer : la cible est atteinte et on est encore dans la fenêtre de tolérance
      - manqués   : la fenêtre de tolérance est dépassée sans mesure
    """
    due, missed = [], []
    for label, hours, tolerance_h in config.CHECKPOINTS:
        if label in done_labels:
            continue
        target = discovered_at + hours * HOUR
        if now < target:
            continue
        if now <= target + tolerance_h * HOUR:
            due.append((label, target))
        else:
            missed.append((label, target))
    return due, missed


def wants_dense_measure(discovered_at: int, now: int, last_measured_at: int, status: str) -> bool:
    """Faut-il une mesure intermédiaire maintenant (pour la courbe de prix) ?"""
    if status != "active":
        return False
    age_h = (now - discovered_at) / HOUR
    since_last_min = (now - last_measured_at) / MINUTE
    if age_h < config.DENSE_FAST_UNTIL_H:
        return since_last_min >= config.DENSE_FAST_GAP_MIN
    if age_h < config.DENSE_SLOW_UNTIL_H:
        return since_last_min >= config.DENSE_SLOW_GAP_MIN
    return False


def tracking_end(discovered_at: int) -> int:
    """Heure après laquelle on arrête complètement de suivre un token."""
    _, hours, tolerance_h = config.CHECKPOINTS[-1]
    return discovered_at + (hours + tolerance_h) * HOUR


def rug_reason(liquidity, liquidity_ref, missing_in_a_row: int,
               price=None, price_t0=None, volume_h1=None) -> str | None:
    """
    Retourne la raison du rug / de la mort du token, ou None s'il a l'air vivant.
    liquidity_ref = liquidité à t0, ou la première connue (les tokens encore sur la
    bonding curve pump.fun n'ont pas de liquidité dans DexScreener).
    """
    if missing_in_a_row >= config.RUG_MISSING_IN_A_ROW:
        return "pair_missing"
    if liquidity is not None and liquidity_ref:
        if liquidity < config.RUG_LIQ_FRACTION * liquidity_ref:
            return "liquidity_drop"
        if liquidity_ref >= config.RUG_LIQ_ABS_APPLIES_FROM_USD and liquidity < config.RUG_LIQ_ABS_USD:
            return "liquidity_low"
    # Token mort sans info de liquidité : prix effondré ET plus personne ne trade.
    if price is not None and price_t0 and volume_h1 is not None:
        if price < config.DEAD_PRICE_FRACTION * price_t0 and volume_h1 < config.DEAD_VOLUME_H1_USD:
            return "dead_no_volume"
    return None
