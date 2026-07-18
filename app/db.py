import sqlite3
from datetime import datetime
from pathlib import Path

from .domain import DEFAULT_WALLETS

DB_PATH = Path(__file__).resolve().parent.parent / "instance" / "rendimientos.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    capture_time TEXT NOT NULL,
    active_weekdays TEXT NOT NULL,
    tna REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_id TEXT NOT NULL REFERENCES wallets(id),
    fecha TEXT NOT NULL,
    monto REAL NOT NULL,
    tna_aplicada REAL NOT NULL,
    rendimiento REAL NOT NULL,
    creado_en TEXT NOT NULL,
    UNIQUE(wallet_id, fecha)
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
            "INSERT INTO wallets (id, name, capture_time, active_weekdays, tna) VALUES (?, ?, ?, ?, ?)",
            (w.id, w.name, w.capture_time, ",".join(map(str, w.active_weekdays)), w.default_tna),
        )


def get_wallets(conn):
    return conn.execute("SELECT * FROM wallets ORDER BY capture_time").fetchall()


def get_wallet(conn, wallet_id):
    return conn.execute("SELECT * FROM wallets WHERE id = ?", (wallet_id,)).fetchone()


def update_wallet_tna(conn, wallet_id, tna):
    conn.execute("UPDATE wallets SET tna = ? WHERE id = ?", (tna, wallet_id))
    conn.commit()


def upsert_capture(conn, wallet_id, fecha, monto, tna_aplicada, rendimiento):
    conn.execute(
        """
        INSERT INTO captures (wallet_id, fecha, monto, tna_aplicada, rendimiento, creado_en)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(wallet_id, fecha) DO UPDATE SET
            monto = excluded.monto,
            tna_aplicada = excluded.tna_aplicada,
            rendimiento = excluded.rendimiento,
            creado_en = excluded.creado_en
        """,
        (wallet_id, fecha, monto, tna_aplicada, rendimiento, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def get_captures_for_date(conn, fecha):
    return conn.execute("SELECT * FROM captures WHERE fecha = ?", (fecha,)).fetchall()


def get_all_captures(conn):
    return conn.execute(
        """
        SELECT captures.*, wallets.name AS wallet_name
        FROM captures
        JOIN wallets ON wallets.id = captures.wallet_id
        ORDER BY fecha DESC, captures.id
        """
    ).fetchall()
