"""
Base SQLite : schéma + petites fonctions de lecture/écriture.
Tous les temps sont des timestamps Unix en secondes (UTC).
Pour les lire en clair dans SQLite : datetime(colonne, 'unixepoch').
"""
import os
import sqlite3

from bot import config

HOLDERS_DDL = """
-- Gros holders "humains" de chaque token à t0 (base de l'analyse des wallets).
-- WITHOUT ROWID : la table est rangée directement par sa clé (2x plus compact).
-- L'index par wallet n'est créé que dans data/all.db (analyse), pas dans les bases du bot.
CREATE TABLE IF NOT EXISTS holder_appearances (
    token_address TEXT NOT NULL REFERENCES tokens(address),
    wallet        TEXT NOT NULL,
    rank          INTEGER,
    pct           REAL,
    is_insider    INTEGER,
    is_creator    INTEGER,
    recorded_at   INTEGER,
    PRIMARY KEY (token_address, wallet)
) WITHOUT ROWID;
"""

SCHEMA = """
-- Tokens vus mais pas encore enregistrés (on attend leur paire DexScreener).
CREATE TABLE IF NOT EXISTS candidates (
    address        TEXT PRIMARY KEY,
    source         TEXT NOT NULL,              -- dex_profile / dex_boost / dex_cto / rugcheck_new
    first_seen_at  INTEGER NOT NULL,
    state          TEXT NOT NULL DEFAULT 'pending',  -- pending / promoted / too_old / expired
    updated_at     INTEGER NOT NULL
);

-- Infos fixes de chaque token suivi (mesurées à la découverte = t0).
CREATE TABLE IF NOT EXISTS tokens (
    address                  TEXT PRIMARY KEY,
    symbol                   TEXT,
    name                     TEXT,
    source                   TEXT,
    first_seen_at            INTEGER,          -- 1re apparition dans une source
    discovered_at            INTEGER NOT NULL, -- moment du snapshot t0
    pair_address             TEXT,             -- paire la plus liquide à t0
    dex_id                   TEXT,
    quote_symbol             TEXT,
    pair_created_at          INTEGER,
    has_website              INTEGER,
    has_twitter              INTEGER,
    has_telegram             INTEGER,
    rc_ok                    INTEGER NOT NULL DEFAULT 0,  -- 1 si RugCheck a répondu
    mint_authority_enabled   INTEGER,
    freeze_authority_enabled INTEGER,
    top10_pct                REAL,             -- % détenu par les 10 plus gros holders (hors pools)
    rc_score                 INTEGER,
    rc_score_norm            INTEGER,
    rc_rugged                INTEGER,
    rc_risks                 TEXT,             -- liste JSON des risques RugCheck
    launchpad                TEXT,
    creator                  TEXT,
    total_holders            INTEGER,
    status                   TEXT NOT NULL DEFAULT 'active',  -- active / rug
    rug_at                   INTEGER,
    rug_reason               TEXT,
    missing_in_a_row         INTEGER NOT NULL DEFAULT 0
);

-- Une ligne par mesure. checkpoint = 't0', '1h', '6h', '24h', '48h', '72h', '7d'
-- ou NULL pour une mesure intermédiaire. Un checkpoint manqué a measured_at NULL.
CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY,
    token_address TEXT NOT NULL REFERENCES tokens(address),
    checkpoint    TEXT,
    target_at     INTEGER,
    measured_at   INTEGER,
    delay_min     REAL,                        -- retard réel par rapport à la cible
    pair_found    INTEGER,
    pair_address  TEXT,
    price_usd     REAL,
    market_cap    REAL,
    fdv           REAL,
    liquidity_usd REAL,
    volume_m5     REAL,
    volume_h1     REAL,
    volume_h24    REAL,
    buys_m5       INTEGER,
    sells_m5      INTEGER,
    buys_h1       INTEGER,
    sells_h1      INTEGER,
    UNIQUE (token_address, checkpoint)         -- (plusieurs NULL autorisés)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_token ON snapshots(token_address, measured_at);

""" + HOLDERS_DDL + """
-- Journal des runs.
CREATE TABLE IF NOT EXISTS runs (
    id                 INTEGER PRIMARY KEY,
    started_at         INTEGER,
    duration_s         REAL,
    new_tokens         INTEGER,
    measurements       INTEGER,
    checkpoints_done   INTEGER,
    checkpoints_missed INTEGER,
    new_rugs           INTEGER,
    errors             INTEGER,
    error_samples      TEXT,
    db_size_kb         INTEGER
);
"""

SNAPSHOT_FIELDS = [
    "pair_address", "price_usd", "market_cap", "fdv", "liquidity_usd",
    "volume_m5", "volume_h1", "volume_h24", "buys_m5", "sells_m5", "buys_h1", "sells_h1",
]


def connect(path: str = config.DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row          # accès aux colonnes par nom : row["price_usd"]
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn) -> None:
    """Met à niveau une base créée par une ancienne version du schéma."""
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'holder_appearances'"
    ).fetchone()[0]
    if "WITHOUT ROWID" not in sql.upper():
        conn.execute("PRAGMA foreign_keys = OFF")   # simple copie de lignes existantes
        conn.executescript("""
            BEGIN;
            DROP INDEX IF EXISTS idx_holders_wallet;
            ALTER TABLE holder_appearances RENAME TO holder_appearances_old;
        """ + HOLDERS_DDL + """
            INSERT INTO holder_appearances SELECT * FROM holder_appearances_old;
            DROP TABLE holder_appearances_old;
            COMMIT;
        """)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("VACUUM")


# --- Candidats -------------------------------------------------------------------

def add_candidate(conn, address: str, source: str, now: int) -> bool:
    """Ajoute un candidat s'il est inconnu. Retourne True s'il est nouveau."""
    if conn.execute("SELECT 1 FROM tokens WHERE address = ?", (address,)).fetchone():
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO candidates (address, source, first_seen_at, updated_at) VALUES (?, ?, ?, ?)",
        (address, source, now, now),
    )
    return cur.rowcount == 1


def expire_candidates(conn, now: int) -> int:
    limit = now - config.CANDIDATE_EXPIRY_H * 3600
    cur = conn.execute(
        "UPDATE candidates SET state = 'expired', updated_at = ? WHERE state = 'pending' AND first_seen_at < ?",
        (now, limit),
    )
    return cur.rowcount


def pending_candidates(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM candidates WHERE state = 'pending' ORDER BY first_seen_at"
    ).fetchall()


def set_candidate_state(conn, address: str, state: str, now: int) -> None:
    conn.execute("UPDATE candidates SET state = ?, updated_at = ? WHERE address = ?", (state, now, address))


# --- Tokens ----------------------------------------------------------------------

def insert_token(conn, row: dict) -> None:
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO tokens ({columns}) VALUES ({placeholders})", list(row.values()))


def insert_holders(conn, token_address: str, holders: list[dict], now: int) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO holder_appearances "
        "(token_address, wallet, rank, pct, is_insider, is_creator, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (token_address, h["wallet"], rank, h["pct"], h["is_insider"], h["is_creator"], now)
            for rank, h in enumerate(holders, start=1)
        ],
    )


def mark_rug(conn, address: str, reason: str, now: int) -> None:
    conn.execute(
        "UPDATE tokens SET status = 'rug', rug_at = ?, rug_reason = ? WHERE address = ? AND status = 'active'",
        (now, reason, address),
    )


def set_missing_count(conn, address: str, count: int) -> None:
    conn.execute("UPDATE tokens SET missing_in_a_row = ? WHERE address = ?", (count, address))


def tokens_to_track(conn, now: int) -> list[sqlite3.Row]:
    """
    Tokens encore dans leur période de suivi, avec :
      - liquidity_ref    : liquidité à t0, sinon la première liquidité connue
      - price_t0         : prix au snapshot t0
      - last_measured_at : dernière mesure réelle
      - done_labels      : checkpoints déjà faits ou manqués (séparés par des virgules)
    """
    from bot.tracker import tracking_end  # import local pour éviter un import circulaire

    rows = conn.execute(
        """
        SELECT t.address, t.discovered_at, t.status, t.missing_in_a_row,
               (SELECT liquidity_usd FROM snapshots
                 WHERE token_address = t.address AND liquidity_usd IS NOT NULL
                 ORDER BY measured_at LIMIT 1)
                   AS liquidity_ref,
               (SELECT price_usd FROM snapshots WHERE token_address = t.address AND checkpoint = 't0')
                   AS price_t0,
               (SELECT MAX(measured_at) FROM snapshots WHERE token_address = t.address)
                   AS last_measured_at,
               (SELECT GROUP_CONCAT(checkpoint) FROM snapshots
                 WHERE token_address = t.address AND checkpoint IS NOT NULL)
                   AS done_labels
        FROM tokens t
        """
    ).fetchall()
    return [r for r in rows if now <= tracking_end(r["discovered_at"])]


# --- Snapshots -------------------------------------------------------------------

def insert_snapshot(conn, token_address: str, checkpoint, target_at, measured_at, metrics: dict | None) -> None:
    """Enregistre une mesure. metrics=None -> paire introuvable (ou checkpoint manqué si measured_at=None)."""
    delay_min = round((measured_at - target_at) / 60, 1) if (measured_at and target_at) else None
    values = {
        "token_address": token_address,
        "checkpoint": checkpoint,
        "target_at": target_at,
        "measured_at": measured_at,
        "delay_min": delay_min,
        "pair_found": None if measured_at is None else int(metrics is not None),
    }
    for field in SNAPSHOT_FIELDS:
        values[field] = metrics.get(field) if metrics else None
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    conn.execute(f"INSERT OR IGNORE INTO snapshots ({columns}) VALUES ({placeholders})", list(values.values()))


# --- Runs ------------------------------------------------------------------------

def insert_run(conn, row: dict) -> None:
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO runs ({columns}) VALUES ({placeholders})", list(row.values()))
