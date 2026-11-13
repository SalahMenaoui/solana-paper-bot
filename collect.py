"""
Point d'entrée d'un run de collecte (lancé toutes les 30 min par GitHub Actions).

Ordre des étapes :
  1. SUIVI      : re-mesurer les tokens déjà connus (urgent : les checkpoints ont une fenêtre)
  2. DÉCOUVERTE : récupérer les nouveaux tokens des différentes sources -> table candidates
  3. SNAPSHOT   : pour les candidats qui ont une paire, mesure t0 + RugCheck + holders
  4. ARCHIVAGE  : les tokens dont le suivi est fini partent dans data/archive/ (voir bot/storage.py)
  5. LOG        : résumé affiché + ligne dans la table runs

PAPER TRADING / RECHERCHE UNIQUEMENT : aucun wallet, aucune clé, aucun trade.
"""
import os
import time

from bot import config, db, dexscreener, rugcheck, storage, tracker
from bot.http_client import ApiError


class RunLog:
    """Compteurs et erreurs du run. Une erreur n'arrête jamais le run entier."""

    def __init__(self):
        self.started_at = int(time.time())
        self.t0 = time.monotonic()
        self.deadline = self.t0 + config.RUN_TIME_BUDGET_S
        self.new_tokens = 0
        self.new_candidates = 0
        self.measurements = 0
        self.checkpoints_done = 0
        self.checkpoints_missed = 0
        self.new_rugs = 0
        self.archived = 0
        self.sol: dict = {}           # prix du SOL au début du run (vide si indisponible)
        self.errors: list[str] = []
        self.notes: list[str] = []

    def error(self, where: str, exc: Exception) -> None:
        message = f"{where}: {exc}"
        self.errors.append(message)
        print(f"  ! {message}")

    def time_left(self) -> float:
        return self.deadline - time.monotonic()


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ---------------------------------------------------------------------------------
# 1. SUIVI
# ---------------------------------------------------------------------------------

def follow_up(conn, log: RunLog) -> None:
    now = int(time.time())
    to_measure = []  # liste de (token_row, checkpoints_dus)

    for token in db.tokens_to_track(conn, now):
        done = set((token["done_labels"] or "").split(",")) - {""}
        due, missed = tracker.due_checkpoints(token["discovered_at"], now, done)

        # Checkpoints dont la fenêtre est dépassée : on les note "manqués", sans inventer de prix.
        for label, target in missed:
            db.insert_snapshot(conn, token["address"], label, target, None, None)
            log.checkpoints_missed += 1

        last = token["last_measured_at"] or token["discovered_at"]
        dense = tracker.wants_dense_measure(token["discovered_at"], now, last, token["status"])
        if due or dense:
            to_measure.append((token, due))
    conn.commit()

    for batch in chunks(to_measure, config.DEX_BATCH_SIZE):
        if log.time_left() < 10:
            log.notes.append("budget temps atteint pendant le suivi")
            break
        try:
            pairs = dexscreener.best_pairs([token["address"] for token, _ in batch])
        except ApiError as exc:
            log.error("suivi (lot DexScreener)", exc)
            continue

        measured_at = int(time.time())
        for token, due in batch:
            try:
                _record_measurement(conn, log, token, due, pairs.get(token["address"]), measured_at)
            except Exception as exc:  # un token en erreur ne bloque pas les autres
                log.error(f"suivi {token['address'][:8]}", exc)
        conn.commit()


def _record_measurement(conn, log: RunLog, token, due, pair, measured_at: int) -> None:
    address = token["address"]
    metrics = dexscreener.pair_metrics(pair) if pair else None

    # Une seule mesure, enregistrée sous chaque checkpoint dû (ou comme mesure intermédiaire).
    for label, target in (due or [(None, None)]):
        db.insert_snapshot(conn, address, label, target, measured_at, metrics)
        if label:
            log.checkpoints_done += 1
    log.measurements += 1

    missing = 0 if pair else token["missing_in_a_row"] + 1
    db.set_missing_count(conn, address, missing)

    if token["status"] == "active":
        m = metrics or {}
        reason = tracker.rug_reason(
            m.get("liquidity_usd"), token["liquidity_ref"], missing,
            price=m.get("price_usd"), price_t0=token["price_t0"], volume_h1=m.get("volume_h1"),
        )
        if reason:
            db.mark_rug(conn, address, reason, measured_at)
            log.new_rugs += 1


# ---------------------------------------------------------------------------------
# 2. DÉCOUVERTE
# ---------------------------------------------------------------------------------

SOURCES = [
    ("dex_profile", dexscreener.latest_profiles),
    ("dex_boost", dexscreener.latest_boosts),
    ("dex_cto", dexscreener.latest_takeovers),
    ("rugcheck_new", rugcheck.new_tokens),
]


def discover(conn, log: RunLog) -> None:
    now = int(time.time())
    for source_name, fetch in SOURCES:
        try:
            for address in fetch():
                if db.add_candidate(conn, address, source_name, now):
                    log.new_candidates += 1
        except ApiError as exc:
            log.error(f"découverte {source_name}", exc)
    db.expire_candidates(conn, now)
    conn.commit()


# ---------------------------------------------------------------------------------
# 3. SNAPSHOT t0
# ---------------------------------------------------------------------------------

def snapshot_new_tokens(conn, log: RunLog) -> None:
    candidates = db.pending_candidates(conn)

    # a) Chercher la paire DexScreener de chaque candidat (30 par appel).
    found: dict[str, dict] = {}
    fetched_at = int(time.time())
    for batch in chunks([c["address"] for c in candidates], config.DEX_BATCH_SIZE):
        try:
            found.update(dexscreener.best_pairs(batch))
        except ApiError as exc:
            log.error("snapshot (lot DexScreener)", exc)

    # b) Garder les paires assez jeunes, les plus jeunes d'abord.
    now = int(time.time())
    eligible = []
    for cand in candidates:
        pair = found.get(cand["address"])
        if not pair or not dexscreener.pair_metrics(pair)["price_usd"]:
            continue  # pas encore de paire (ou pas de prix) : on réessaiera au prochain run
        created_ms = pair.get("pairCreatedAt")
        if not created_ms or (now - created_ms / 1000) / 3600 > config.MAX_PAIR_AGE_H:
            db.set_candidate_state(conn, cand["address"], "too_old", now)
            continue
        eligible.append((cand, pair))
    conn.commit()
    eligible.sort(key=lambda item: item[1]["pairCreatedAt"], reverse=True)

    # c) RugCheck + enregistrement, dans la limite du budget.
    rugcheck_failures_in_a_row = 0
    for cand, pair in eligible[: config.MAX_NEW_TOKENS_PER_RUN]:
        if log.time_left() < config.RUGCHECK_INTERVAL_S + 15:
            log.notes.append("budget temps atteint : candidats restants reportés au prochain run")
            break
        if rugcheck_failures_in_a_row >= 3:
            log.notes.append("RugCheck indisponible : candidats reportés au prochain run")
            break
        address = cand["address"]
        try:
            report = rugcheck.report(address)
            rugcheck_failures_in_a_row = 0
        except (ApiError, ValueError) as exc:
            # Sans RugCheck, on ne crée pas le token : il reste "pending" et sera retenté.
            rugcheck_failures_in_a_row += 1
            log.error(f"rugcheck {address[:8]}", exc)
            continue

        try:
            _save_new_token(conn, cand, pair, report, fetched_at, log.sol.get("sol_price_usd"))
            conn.commit()
            log.new_tokens += 1
            if report["rc_rugged"]:
                log.new_rugs += 1
        except Exception as exc:
            conn.rollback()
            log.error(f"enregistrement {address[:8]}", exc)


def _save_new_token(conn, cand, pair, report: dict, measured_at: int, sol_price) -> None:
    address = cand["address"]
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    token_row = {
        "address": address,
        "symbol": base.get("symbol"),
        "name": base.get("name"),
        "source": cand["source"],
        "first_seen_at": cand["first_seen_at"],
        "discovered_at": measured_at,
        "pair_address": pair.get("pairAddress"),
        "dex_id": pair.get("dexId"),
        "quote_symbol": quote.get("symbol"),
        "pair_created_at": int(pair["pairCreatedAt"] / 1000),
        **dexscreener.socials(pair),
        "rc_ok": 1,
        **{k: v for k, v in report.items() if k != "holders"},
        "sol_price_usd": sol_price,
    }
    if report["rc_rugged"]:
        token_row.update(status="rug", rug_at=measured_at, rug_reason="rugcheck_rugged")

    db.insert_token(conn, token_row)
    db.insert_snapshot(conn, address, "t0", measured_at, measured_at, dexscreener.pair_metrics(pair))
    db.insert_holders(conn, address, report["holders"], measured_at)
    db.set_candidate_state(conn, address, "promoted", measured_at)


# ---------------------------------------------------------------------------------
# 4. ARCHIVAGE
# ---------------------------------------------------------------------------------

def archive(conn, log: RunLog) -> None:
    log.archived = storage.archive_finished(conn, int(time.time()))


# ---------------------------------------------------------------------------------
# 5. LOG
# ---------------------------------------------------------------------------------

def finish(conn, log: RunLog) -> None:
    duration = round(time.monotonic() - log.t0, 1)
    size_kb = os.path.getsize(config.DB_PATH) // 1024 if os.path.exists(config.DB_PATH) else 0
    db.insert_run(conn, {
        "started_at": log.started_at,
        "duration_s": duration,
        "new_tokens": log.new_tokens,
        "measurements": log.measurements,
        "checkpoints_done": log.checkpoints_done,
        "checkpoints_missed": log.checkpoints_missed,
        "new_rugs": log.new_rugs,
        "errors": len(log.errors),
        "error_samples": "\n".join(log.errors[:10]),
        "db_size_kb": size_kb,
        "new_candidates": log.new_candidates,
        **log.sol,
    })
    conn.commit()

    print("\n===== Résumé du run =====")
    print(f"Nouveaux candidats vus    : {log.new_candidates}")
    print(f"Nouveaux tokens (t0)      : {log.new_tokens}")
    print(f"Mesures de suivi          : {log.measurements}")
    print(f"Checkpoints faits/manqués : {log.checkpoints_done} / {log.checkpoints_missed}")
    print(f"Nouveaux rugs détectés    : {log.new_rugs}")
    print(f"Tokens archivés           : {log.archived}")
    print(f"Prix du SOL               : {log.sol.get('sol_price_usd')} $ ({log.sol.get('sol_change_h24')} % sur 24 h)")
    print(f"Erreurs                   : {len(log.errors)}")
    for note in log.notes:
        print(f"Note                      : {note}")
    print(f"Durée                     : {duration} s | taille base : {size_kb} Ko")


def main() -> None:
    storage.gunzip_if_needed(config.DB_PATH)
    conn = db.connect()
    log = RunLog()
    try:
        log.sol = dexscreener.sol_market()
    except ApiError as exc:
        log.error("prix du SOL", exc)
    # Chaque étape est protégée : si l'une plante, les suivantes tournent quand même.
    for step_name, step in [("suivi", follow_up), ("découverte", discover), ("snapshot", snapshot_new_tokens),
                            ("archivage", archive)]:
        print(f"--- {step_name} ---")
        try:
            step(conn, log)
        except Exception as exc:
            conn.rollback()
            log.error(f"étape {step_name}", exc)
    finish(conn, log)
    conn.close()


if __name__ == "__main__":
    main()
