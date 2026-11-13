"""
Stockage des données sur la durée.

Pourquoi : GitHub refuse les fichiers > 100 Mo, et committer une grosse base toutes les
30 min ferait exploser l'historique Git. Donc :
  - data/bot.db (live) ne garde que les tokens encore suivis (< 8 jours) -> reste petite ;
  - à la fin de son suivi, un token (+ ses snapshots + ses holders) est DÉPLACÉ dans
    data/archive/bot-AAAA-MM.db (mois de sa découverte, UTC) -> ces fichiers ne bougent plus
    une fois le mois fini ;
  - sur GitHub, tout est stocké compressé (.gz) sur la branche "data", qui n'a qu'un seul
    commit (réécrit à chaque run) -> l'historique ne grossit pas.

Commandes (utilisées par le workflow GitHub Actions) :
  python -m bot.storage unpack   décompresse la base live (les archives le sont à la demande)
  python -m bot.storage pack     recompresse toutes les bases décompressées
"""
import glob
import gzip
import os
import shutil
import sqlite3
import sys
import time

from bot import config, db
from bot.tracker import tracking_end

SNAPSHOT_COLUMNS = [
    "token_address", "checkpoint", "target_at", "measured_at", "delay_min", "pair_found",
    *db.SNAPSHOT_FIELDS,
]


def month_of(ts: int) -> str:
    return time.strftime("%Y-%m", time.gmtime(ts))


def archive_path(month: str, archive_dir: str = config.ARCHIVE_DIR) -> str:
    return os.path.join(archive_dir, f"bot-{month}.db")


# --- Compression -----------------------------------------------------------------

def gunzip_if_needed(path: str) -> None:
    """Si seul path.gz existe, le décompresse en path (et supprime le .gz)."""
    gz = path + ".gz"
    if os.path.exists(path) or not os.path.exists(gz):
        return
    with gzip.open(gz, "rb") as src, open(path + ".tmp", "wb") as dst:
        shutil.copyfileobj(src, dst)
    os.replace(path + ".tmp", path)
    os.remove(gz)


def gzip_file(path: str) -> None:
    with open(path, "rb") as src, gzip.open(path + ".gz.tmp", "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    os.replace(path + ".gz.tmp", path + ".gz")
    os.remove(path)


def unpack(data_dir: str = config.DATA_DIR) -> None:
    gunzip_if_needed(os.path.join(data_dir, "bot.db"))


def pack(data_dir: str = config.DATA_DIR) -> None:
    """Compresse chaque .db décompressé (live + archives touchées pendant le run)."""
    paths = glob.glob(os.path.join(data_dir, "*.db")) + glob.glob(os.path.join(data_dir, "archive", "*.db"))
    for path in paths:
        if os.path.basename(path) == "all.db":
            continue
        gzip_file(path)
        print(f"compressé : {path}.gz ({os.path.getsize(path + '.gz') // 1024} Ko)")


# --- Archivage -------------------------------------------------------------------

def archive_finished(conn: sqlite3.Connection, now: int, archive_dir: str = config.ARCHIVE_DIR) -> int:
    """
    Déplace vers l'archive du mois les tokens dont le suivi est terminé, et fait le ménage
    dans la base live (vieux candidats, vieux runs). Retourne le nombre de tokens archivés.
    Chaque mois est traité en une seule transaction (base live + archive) : soit tout est
    déplacé, soit rien. Rejouer l'opération ne crée pas de doublons.
    """
    by_month: dict[str, list[str]] = {}
    for row in conn.execute("SELECT address, discovered_at FROM tokens"):
        if now > tracking_end(row["discovered_at"]):
            by_month.setdefault(month_of(row["discovered_at"]), []).append(row["address"])

    runs_limit = now - config.RUNS_RETENTION_DAYS * 86400
    for row in conn.execute("SELECT DISTINCT started_at FROM runs WHERE started_at < ?", (runs_limit,)):
        by_month.setdefault(month_of(row["started_at"]), [])

    archived = 0
    for month, addresses in sorted(by_month.items()):
        path = archive_path(month, archive_dir)
        gunzip_if_needed(path)
        db.connect(path).close()           # crée le fichier + le schéma si besoin
        _move_month(conn, path, month, addresses, runs_limit)
        archived += len(addresses)

    conn.execute(
        "DELETE FROM candidates WHERE state != 'pending' AND updated_at < ?",
        (now - config.CANDIDATE_RETENTION_H * 3600,),
    )
    conn.commit()
    return archived


def _move_month(conn, path: str, month: str, addresses: list[str], runs_limit: int) -> None:
    conn.commit()
    conn.execute("ATTACH DATABASE ? AS arc", (path,))
    try:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS moving (address TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM temp.moving")
        conn.executemany("INSERT INTO temp.moving VALUES (?)", [(a,) for a in addresses])
        cols = ", ".join(SNAPSHOT_COLUMNS)
        tok = ", ".join(db.columns(conn, "tokens"))
        hold = ", ".join(db.columns(conn, "holder_appearances"))
        in_moving = "IN (SELECT address FROM moving)"
        statements = [
            # Idempotent : on efface d'abord ce que l'archive aurait déjà pour ces tokens.
            f"DELETE FROM arc.snapshots WHERE token_address {in_moving}",
            f"DELETE FROM arc.holder_appearances WHERE token_address {in_moving}",
            f"DELETE FROM arc.tokens WHERE address {in_moving}",
            f"INSERT INTO arc.tokens ({tok}) SELECT {tok} FROM main.tokens WHERE address {in_moving}",
            # Sans la colonne id : les id de la base live peuvent être réutilisés.
            f"INSERT INTO arc.snapshots ({cols}) SELECT {cols} FROM main.snapshots WHERE token_address {in_moving}",
            f"INSERT INTO arc.holder_appearances ({hold}) SELECT {hold} FROM main.holder_appearances "
            f"WHERE token_address {in_moving}",
            f"DELETE FROM main.snapshots WHERE token_address {in_moving}",
            f"DELETE FROM main.holder_appearances WHERE token_address {in_moving}",
            f"DELETE FROM main.tokens WHERE address {in_moving}",
        ]
        month_runs = ("started_at < ? AND strftime('%Y-%m', started_at, 'unixepoch') = ?", (runs_limit, month))
        run_cols = ", ".join(c for c in db.columns(conn, "runs") if c != "id")
        # Une seule transaction (ouverte implicitement par le 1er DELETE), sur les 2 bases.
        for sql in statements:
            conn.execute(sql)
        conn.execute(f"INSERT INTO arc.runs ({run_cols}) SELECT {run_cols} FROM main.runs WHERE {month_runs[0]}",
                     month_runs[1])
        conn.execute(f"DELETE FROM main.runs WHERE {month_runs[0]}", month_runs[1])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("DETACH DATABASE arc")


# --- Fusion pour l'analyse ----------------------------------------------------------

def merge_all(data_dir: str = config.DATA_DIR, out_path: str | None = None) -> str:
    """Construit data/all.db = base live + toutes les archives (pour stats.py / wallets.py)."""
    out_path = out_path or os.path.join(data_dir, "all.db")
    sources = sorted(glob.glob(os.path.join(data_dir, "archive", "bot-*.db")))
    live = os.path.join(data_dir, "bot.db")
    if os.path.exists(live):
        sources.append(live)

    tmp = out_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = db.connect(tmp)
    for src in sources:
        db.connect(src).close()            # met le schéma de la source à jour si besoin
        conn.execute("ATTACH DATABASE ? AS src", (src,))
        for table, verb, skip in [("tokens", "INSERT OR REPLACE", ()), ("snapshots", "INSERT", ("id",)),
                                  ("holder_appearances", "INSERT OR REPLACE", ()), ("runs", "INSERT", ("id",))]:
            cols = ", ".join(c for c in db.columns(conn, table) if c not in skip)
            conn.execute(f"{verb} INTO {table} ({cols}) SELECT {cols} FROM src.{table}")
        conn.commit()
        conn.execute("DETACH DATABASE src")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holders_wallet ON holder_appearances(wallet)")
    conn.commit()
    conn.close()
    os.replace(tmp, out_path)
    return out_path


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "unpack":
        unpack()
    elif command == "pack":
        pack()
    else:
        sys.exit("usage : python -m bot.storage unpack|pack")
