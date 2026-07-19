import sqlite3
from datetime import date
from pathlib import Path

from .domain import DEFAULT_WALLETS, MovimientoRecurrente

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

CREATE TABLE IF NOT EXISTS egresos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    etiqueta TEXT NOT NULL,
    monto REAL NOT NULL,
    recurrente INTEGER NOT NULL DEFAULT 0,
    fecha TEXT,
    dia_mes INTEGER
);

CREATE TABLE IF NOT EXISTS ingresos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    etiqueta TEXT NOT NULL,
    monto REAL NOT NULL,
    recurrente INTEGER NOT NULL DEFAULT 0,
    fecha TEXT,
    dia_mes INTEGER
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
    _migrate_legacy_egresos(conn)
    conn.commit()
    conn.close()


def _migrate_legacy_egresos(conn):
    """Traslada datos del esquema viejo (impuestos puntuales + cuota_fija/sueldo
    únicos) al nuevo esquema de egresos/ingresos etiquetados, y borra las
    tablas viejas. No-op si ya fueron migradas (o nunca existieron)."""
    tablas = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    if "impuestos" in tablas:
        for row in conn.execute("SELECT fecha, monto FROM impuestos"):
            conn.execute(
                "INSERT INTO egresos (etiqueta, monto, recurrente, fecha, dia_mes) "
                "VALUES ('Impuesto', ?, 0, ?, NULL)",
                (row["monto"], row["fecha"]),
            )
        conn.execute("DROP TABLE impuestos")

    if "config_egresos" in tablas:
        row = conn.execute("SELECT cuota_fija, sueldo FROM config_egresos WHERE id = 1").fetchone()
        if row is not None:
            if row["cuota_fija"] > 0:
                conn.execute(
                    "INSERT INTO egresos (etiqueta, monto, recurrente, fecha, dia_mes) "
                    "VALUES ('Cuota fija', ?, 1, NULL, 31)",
                    (row["cuota_fija"],),
                )
            if row["sueldo"] > 0:
                conn.execute(
                    "INSERT INTO ingresos (etiqueta, monto, recurrente, fecha, dia_mes) "
                    "VALUES ('Sueldo', ?, 1, NULL, 5)",
                    (row["sueldo"],),
                )
        conn.execute("DROP TABLE config_egresos")


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


def update_aporte(conn, aporte_id, fecha, monto):
    conn.execute(
        "UPDATE aportes SET fecha = ?, monto = ? WHERE id = ?",
        (fecha.isoformat(), monto, aporte_id),
    )
    conn.commit()


def delete_aporte(conn, aporte_id):
    conn.execute("DELETE FROM aportes WHERE id = ?", (aporte_id,))
    conn.commit()


def _row_to_movimiento(row) -> MovimientoRecurrente:
    return MovimientoRecurrente(
        etiqueta=row["etiqueta"],
        monto=row["monto"],
        recurrente=bool(row["recurrente"]),
        fecha=date.fromisoformat(row["fecha"]) if row["fecha"] else None,
        dia_mes=row["dia_mes"],
    )


def _get_movimientos(conn, tabla):
    rows = conn.execute(
        f"SELECT * FROM {tabla} ORDER BY recurrente DESC, fecha, dia_mes, id"
    ).fetchall()
    return [(row["id"], _row_to_movimiento(row)) for row in rows]


def _add_movimiento(conn, tabla, etiqueta, monto, recurrente, fecha, dia_mes):
    conn.execute(
        f"INSERT INTO {tabla} (etiqueta, monto, recurrente, fecha, dia_mes) VALUES (?, ?, ?, ?, ?)",
        (etiqueta, monto, int(recurrente), fecha.isoformat() if fecha else None, dia_mes),
    )
    conn.commit()


def _update_movimiento(conn, tabla, movimiento_id, etiqueta, monto, recurrente, fecha, dia_mes):
    conn.execute(
        f"UPDATE {tabla} SET etiqueta = ?, monto = ?, recurrente = ?, fecha = ?, dia_mes = ? "
        "WHERE id = ?",
        (
            etiqueta,
            monto,
            int(recurrente),
            fecha.isoformat() if fecha else None,
            dia_mes,
            movimiento_id,
        ),
    )
    conn.commit()


def _delete_movimiento(conn, tabla, movimiento_id):
    conn.execute(f"DELETE FROM {tabla} WHERE id = ?", (movimiento_id,))
    conn.commit()


def get_egresos(conn):
    return _get_movimientos(conn, "egresos")


def add_egreso(conn, etiqueta, monto, recurrente, fecha, dia_mes):
    _add_movimiento(conn, "egresos", etiqueta, monto, recurrente, fecha, dia_mes)


def update_egreso(conn, egreso_id, etiqueta, monto, recurrente, fecha, dia_mes):
    _update_movimiento(conn, "egresos", egreso_id, etiqueta, monto, recurrente, fecha, dia_mes)


def delete_egreso(conn, egreso_id):
    _delete_movimiento(conn, "egresos", egreso_id)


def get_ingresos(conn):
    return _get_movimientos(conn, "ingresos")


def add_ingreso(conn, etiqueta, monto, recurrente, fecha, dia_mes):
    _add_movimiento(conn, "ingresos", etiqueta, monto, recurrente, fecha, dia_mes)


def update_ingreso(conn, ingreso_id, etiqueta, monto, recurrente, fecha, dia_mes):
    _update_movimiento(conn, "ingresos", ingreso_id, etiqueta, monto, recurrente, fecha, dia_mes)


def delete_ingreso(conn, ingreso_id):
    _delete_movimiento(conn, "ingresos", ingreso_id)
