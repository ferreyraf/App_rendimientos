import sqlite3
from datetime import date
from pathlib import Path

from .domain import DEFAULT_WALLETS

DB_PATH = Path(__file__).resolve().parent.parent / "instance" / "rendimientos.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    capture_time TEXT NOT NULL,
    payout_time TEXT NOT NULL,
    active_weekdays TEXT NOT NULL,
    tna REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rulo_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    principal REAL NOT NULL,
    start_date TEXT NOT NULL
);
"""


def get_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    _seed_wallets(conn)
    conn.commit()
    conn.close()


def _seed_wallets(conn):
    if conn.execute("SELECT id FROM wallets").fetchone():
        return
    for w in DEFAULT_WALLETS:
        conn.execute(
            "INSERT INTO wallets (id, name, capture_time, payout_time, active_weekdays, tna) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                w.id,
                w.name,
                w.capture_time,
                w.payout_time,
                ",".join(map(str, w.active_weekdays)),
                w.default_tna,
            ),
        )


def get_wallets(conn):
    return conn.execute("SELECT * FROM wallets ORDER BY capture_time").fetchall()


def get_wallet(conn, wallet_id):
    return conn.execute("SELECT * FROM wallets WHERE id = ?", (wallet_id,)).fetchone()


def update_wallet(conn, wallet_id, tna, capture_time, payout_time):
    conn.execute(
        "UPDATE wallets SET tna = ?, capture_time = ?, payout_time = ? WHERE id = ?",
        (tna, capture_time, payout_time, wallet_id),
    )
    conn.commit()


def get_config(conn):
    row = conn.execute("SELECT principal, start_date FROM rulo_config WHERE id = 1").fetchone()
    if row is None:
        return None
    return row["principal"], date.fromisoformat(row["start_date"])


def set_config(conn, principal, start_date):
    conn.execute(
        "INSERT INTO rulo_config (id, principal, start_date) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET principal = excluded.principal, "
        "start_date = excluded.start_date",
        (principal, start_date.isoformat()),
    )
    conn.commit()
