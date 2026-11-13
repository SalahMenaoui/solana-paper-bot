"""Tests de l'archivage / compression / fusion. Lancer avec : python -m pytest"""
import os
import sqlite3

from bot import db, storage

DAY = 86400
NOW = 1_795_000_000              # 2026-11-18 (UTC)
OLD = NOW - 10 * DAY             # suivi fini -> doit partir en archive (2026-11)
RECENT = NOW - 2 * DAY           # encore suivi -> reste dans la base live


def add_token(conn, address, discovered_at):
    db.insert_token(conn, {"address": address, "symbol": address.upper(), "discovered_at": discovered_at})
    db.insert_snapshot(conn, address, "t0", discovered_at, discovered_at, {"price_usd": 1.0})
    db.insert_snapshot(conn, address, None, None, discovered_at + 1800, {"price_usd": 2.0})
    db.insert_holders(conn, address, [{"wallet": "w1", "pct": 5.0, "is_insider": 0, "is_creator": 0}], discovered_at)
    conn.commit()


def make_live(tmp_path):
    conn = db.connect(str(tmp_path / "bot.db"))
    add_token(conn, "old", OLD)
    add_token(conn, "recent", RECENT)
    db.insert_run(conn, {"started_at": NOW - 30 * DAY, "new_tokens": 3})   # vieux run (2026-10)
    db.insert_run(conn, {"started_at": NOW - 3600, "new_tokens": 1})       # run récent
    conn.commit()
    return conn


def count(path, table, where="1"):
    with sqlite3.connect(path) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


def test_archivage_deplace_seulement_les_tokens_finis(tmp_path):
    conn = make_live(tmp_path)
    archive_dir = str(tmp_path / "archive")
    assert storage.archive_finished(conn, NOW, archive_dir) == 1

    live = str(tmp_path / "bot.db")
    arc = storage.archive_path("2026-11", archive_dir)
    assert count(live, "tokens") == 1 and count(live, "tokens", "address='recent'") == 1
    assert count(live, "snapshots") == 2 and count(live, "holder_appearances") == 1
    assert count(arc, "tokens", "address='old'") == 1
    assert count(arc, "snapshots") == 2 and count(arc, "holder_appearances") == 1
    # Le vieux run part dans l'archive de son mois, le récent reste.
    assert count(live, "runs") == 1
    assert count(storage.archive_path("2026-10", archive_dir), "runs") == 1


def test_archivage_rejoue_sans_doublons(tmp_path):
    conn = make_live(tmp_path)
    archive_dir = str(tmp_path / "archive")
    storage.archive_finished(conn, NOW, archive_dir)
    # Le même token réapparaît dans la base live (ex. run interrompu puis rejoué).
    add_token(conn, "old", OLD)
    storage.archive_finished(conn, NOW, archive_dir)
    arc = storage.archive_path("2026-11", archive_dir)
    assert count(arc, "tokens") == 1 and count(arc, "snapshots") == 2


def test_archive_compressee_est_reutilisee(tmp_path):
    conn = make_live(tmp_path)
    archive_dir = str(tmp_path / "archive")
    storage.archive_finished(conn, NOW, archive_dir)
    storage.pack(str(tmp_path))
    assert not os.path.exists(storage.archive_path("2026-11", archive_dir))

    storage.unpack(str(tmp_path))
    conn = db.connect(str(tmp_path / "bot.db"))
    add_token(conn, "old2", OLD)
    storage.archive_finished(conn, NOW, archive_dir)
    # L'archive .gz a été décompressée puis complétée, pas écrasée.
    assert count(storage.archive_path("2026-11", archive_dir), "tokens") == 2


def test_fusion(tmp_path):
    conn = make_live(tmp_path)
    storage.archive_finished(conn, NOW, str(tmp_path / "archive"))
    conn.close()
    merged = storage.merge_all(str(tmp_path))
    assert count(merged, "tokens") == 2
    assert count(merged, "snapshots") == 4
    assert count(merged, "holder_appearances") == 2
    assert count(merged, "runs") == 2


def test_migration_table_holders(tmp_path):
    path = str(tmp_path / "ancienne.db")
    with sqlite3.connect(path) as c:
        c.executescript("""
            CREATE TABLE holder_appearances (
                token_address TEXT NOT NULL, wallet TEXT NOT NULL, rank INTEGER, pct REAL,
                is_insider INTEGER, is_creator INTEGER, recorded_at INTEGER,
                PRIMARY KEY (token_address, wallet));
            CREATE INDEX idx_holders_wallet ON holder_appearances(wallet);
            INSERT INTO holder_appearances VALUES ('t', 'w', 1, 5.0, 0, 0, 0);
        """)
    db.connect(path).close()
    with sqlite3.connect(path) as c:
        sql = c.execute("SELECT sql FROM sqlite_master WHERE name='holder_appearances'").fetchone()[0]
        assert "WITHOUT ROWID" in sql
        assert c.execute("SELECT COUNT(*) FROM holder_appearances").fetchone()[0] == 1
