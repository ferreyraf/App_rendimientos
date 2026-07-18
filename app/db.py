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
    tna REAL NOT NULL,
    bundles_weekend_payout INTEGER NOT NULL DEFAULT 0,
    activo INTEGER NOT NULL DEFAULT 1,
    monto_minimo REAL NOT NULL DEFAULT 0,
    monto_maximo REAL,
    reparto_socio_id TEXT,
    reparto_umbral REAL,
    reparto_hora TEXT
);

CREATE TABLE IF NOT EXISTS aportes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    monto REAL NOT NULL
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
            "INSERT INTO wallets "
            "(id, name, capture_time, payout_time, active_weekdays, tna, bundles_weekend_payout, "
            "activo, monto_minimo, monto_maximo, reparto_socio_id, reparto_umbral, reparto_hora) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                w.id,
                w.name,
                w.capture_time,
                w.payout_time,
                ",".join(map(str, w.active_weekdays)),
                w.default_tna,
                int(w.bundles_weekend_payout),
                int(w.activo),
                w.monto_minimo,
                w.monto_maximo,
                w.reparto_socio_id,
                w.reparto_umbral,
                w.reparto_hora,
            ),
        )


def get_wallets(conn):
    return conn.execute("SELECT * FROM wallets ORDER BY capture_time").fetchall()


def get_wallet(conn, wallet_id):
    return conn.execute("SELECT * FROM wallets WHERE id = ?", (wallet_id,)).fetchone()


def update_wallet(
    conn, wallet_id, tna, capture_time, payout_time, activo, monto_minimo, monto_maximo
):
    conn.execute(
        "UPDATE wallets SET tna = ?, capture_time = ?, payout_time = ?, activo = ?, "
        "monto_minimo = ?, monto_maximo = ? WHERE id = ?",
        (tna, capture_time, payout_time, int(activo), monto_minimo, monto_maximo, wallet_id),
    )
    conn.commit()


def get_aportes(conn):
    rows = conn.execute("SELECT id, fecha, monto FROM aportes ORDER BY fecha, id").fetchall()
    return [(row["id"], date.fromisoformat(row["fecha"]), row["monto"]) for row in rows]


def add_aporte(conn, fecha, monto):
    conn.execute(
        "INSERT INTO aportes (fecha, monto) VALUES (?, ?)", (fecha.isoformat(), monto)
    )
    conn.commit()


def delete_aporte(conn, aporte_id):
    conn.execute("DELETE FROM aportes WHERE id = ?", (aporte_id,))
    conn.commit()
