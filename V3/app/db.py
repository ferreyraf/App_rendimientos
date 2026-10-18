import logging
import os
import shutil
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from flask import g

from .domain import DEFAULT_WALLETS, MovimientoRecurrente
from .version import APP_ID


def _data_dir() -> Path:
    """Carpeta de datos (base, backups, logs, clave de sesión).

    - `RULO_DATA_DIR` la fuerza (pruebas, o para usar otra carpeta).
    - App instalada (empaquetada): la carpeta de datos del usuario del
      sistema, así sobrevive a actualizaciones/desinstalaciones y no requiere
      permisos de escritura donde está instalado el programa.
    - Desarrollo: `instance/` junto al código, como siempre.
    """
    if os.environ.get("RULO_DATA_DIR"):
        return Path(os.environ["RULO_DATA_DIR"]).expanduser()
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        return base / APP_ID
    return Path(__file__).resolve().parent.parent / "instance"


DATA_DIR = _data_dir()
DB_PATH = DATA_DIR / "rendimientos.db"
BACKUP_DIR = DATA_DIR / "backups"
MAX_BACKUPS = 10

logger = logging.getLogger(__name__)

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
    dia_mes INTEGER,
    dia_habil INTEGER NOT NULL DEFAULT 0,
    categoria TEXT NOT NULL DEFAULT '',
    aumento_pct REAL NOT NULL DEFAULT 0,
    aumento_inicio TEXT,
    aumento_periodo_meses INTEGER NOT NULL DEFAULT 4,
    moneda TEXT NOT NULL DEFAULT 'ARS'
);

CREATE TABLE IF NOT EXISTS ingresos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    etiqueta TEXT NOT NULL,
    monto REAL NOT NULL,
    recurrente INTEGER NOT NULL DEFAULT 0,
    fecha TEXT,
    dia_mes INTEGER,
    dia_habil INTEGER NOT NULL DEFAULT 0,
    aumento_pct REAL NOT NULL DEFAULT 0,
    aumento_inicio TEXT,
    aumento_periodo_meses INTEGER NOT NULL DEFAULT 4,
    moneda TEXT NOT NULL DEFAULT 'ARS'
);

-- % real aplicado en un ajuste periódico puntual de un egreso/ingreso
-- (ver MovimientoRecurrente.ajustes). `fecha` es el primer día del mes.
CREATE TABLE IF NOT EXISTS ajustes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tabla TEXT NOT NULL,
    movimiento_id INTEGER NOT NULL,
    fecha TEXT NOT NULL,
    pct REAL NOT NULL,
    UNIQUE (tabla, movimiento_id, fecha)
);

-- Última cotización conocida de cada moneda extranjera, en pesos por unidad
-- (ver app/cotizacion.py). `publicado` es la fecha de la fuente.
CREATE TABLE IF NOT EXISTS cotizaciones (
    moneda TEXT PRIMARY KEY,
    valor REAL NOT NULL,
    fuente TEXT NOT NULL,
    publicado TEXT NOT NULL,
    actualizado TEXT NOT NULL
);

-- Monto real (en pesos) de una ocurrencia puntual de un egreso/ingreso: lo
-- que efectivamente se pagó o cobró ese día. `clave` distingue ocurrencias
-- derivadas del mismo movimiento ('aguinaldo'); '' es la ocurrencia normal.
CREATE TABLE IF NOT EXISTS pagos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tabla TEXT NOT NULL,
    movimiento_id INTEGER NOT NULL,
    fecha TEXT NOT NULL,
    clave TEXT NOT NULL DEFAULT '',
    monto REAL NOT NULL,
    registrado TEXT NOT NULL,
    UNIQUE (tabla, movimiento_id, fecha, clave)
);

-- Saldo real total del rulo a una fecha, cargado a mano: la simulación se
-- recalibra a ese valor al cierre de ese día.
CREATE TABLE IF NOT EXISTS controles_saldo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    monto REAL NOT NULL,
    registrado TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inversiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    etiqueta TEXT NOT NULL,
    monto REAL NOT NULL,
    tna REAL NOT NULL DEFAULT 0
);

-- Personas que usan la app. Por ahora una sola (la local, id 1); cuando la
-- app esté en un servidor cada una tendrá sus propios datos, así que todas
-- las tablas de Rutina y Entrenamiento llevan `usuario_id` desde el inicio.
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    creado TEXT NOT NULL
);
"""

USUARIO_LOCAL = 1


def usuario_actual() -> int:
    """Dueño de los datos del pedido en curso. Hoy la app es de una sola
    persona; con login (app en un servidor) saldrá de la sesión, y las
    consultas de las secciones nuevas ya filtran por este valor."""
    return USUARIO_LOCAL


def _connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """Conexión cacheada en `g`: una por request, cerrada siempre por
    `close_db` en el teardown de la app (incluso si la request termina en
    excepción), así ninguna ruta necesita cerrarla a mano."""
    if "db" not in g:
        g.db = _connect()
    return g.db


def close_db(exception=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    _backup_before_migration()
    conn = _connect()
    conn.executescript(SCHEMA)
    _seed_usuario_local(conn)
    # Tablas de las secciones nuevas (importadas acá: esos módulos no
    # dependen de db, pero db tampoco debería cargarlos al importarse).
    from .rutina.datos import SCHEMA as SCHEMA_RUTINA

    conn.executescript(SCHEMA_RUTINA)
    _seed_wallets(conn)
    _migrate_legacy_egresos(conn)
    _migrate_dia_habil(conn)
    _migrate_categoria(conn)
    _migrate_aumento_cuatrimestral(conn)
    _migrate_moneda(conn)
    _migrate_cuotas_y_aguinaldo(conn)
    _migrate_personal_pay_weekday(conn)
    _migrate_mercadopago_weekday(conn)
    _migrate_new_wallet(conn)
    conn.commit()
    conn.close()


def _backup_before_migration():
    """Copia la base actual a instance/backups/ antes de correr las
    migraciones de init_db(), por si alguna sale mal. Nunca debe bloquear el
    arranque: cualquier error de I/O queda logueado y se ignora."""
    if not DB_PATH.exists():
        return
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destino = BACKUP_DIR / f"rendimientos_{timestamp}.db"
        shutil.copy2(DB_PATH, destino)

        backups = sorted(BACKUP_DIR.glob("rendimientos_*.db"))
        for viejo in backups[:-MAX_BACKUPS]:
            viejo.unlink()
    except OSError:
        logger.warning("No se pudo crear el backup de la base de datos", exc_info=True)


def _migrate_personal_pay_weekday(conn):
    """Personal Pay opera de lunes a viernes, no fin de semana: corrige la
    configuración inicial (que la trataba como socia de fin de semana de
    Nx) sin pisar cambios manuales ya hechos desde 'Actualizar billetera'.
    No-op si ya fue migrada."""
    personal_pay = conn.execute(
        "SELECT active_weekdays FROM wallets WHERE id = 'personal_pay'"
    ).fetchone()
    if personal_pay is not None and personal_pay["active_weekdays"] == "5,6":
        conn.execute(
            "UPDATE wallets SET active_weekdays = '0,1,2,3,4', bundles_weekend_payout = 1 "
            "WHERE id = 'personal_pay'"
        )

    nx = conn.execute("SELECT reparto_socio_id FROM wallets WHERE id = 'nx'").fetchone()
    if nx is not None and nx["reparto_socio_id"] == "personal_pay":
        conn.execute(
            "UPDATE wallets SET reparto_socio_id = NULL, reparto_umbral = NULL, "
            "reparto_hora = NULL WHERE id = 'nx'"
        )


def _migrate_mercadopago_weekday(conn):
    """MercadoPago se usa solo los viernes, para que el lunes acredite los 3
    días de rendimiento acumulados (viernes+sábado+domingo) de una sola vez:
    corrige la configuración inicial (que la trataba como billetera de lunes
    a viernes) sin pisar cambios manuales ya hechos desde 'Actualizar
    billetera'. No-op si ya fue migrada."""
    mercadopago = conn.execute(
        "SELECT active_weekdays FROM wallets WHERE id = 'mercadopago'"
    ).fetchone()
    if mercadopago is not None and mercadopago["active_weekdays"] == "0,1,2,3,4":
        conn.execute("UPDATE wallets SET active_weekdays = '4' WHERE id = 'mercadopago'")


def _migrate_new_wallet(conn):
    """Agrega la billetera 'new_wallet' (lunes a viernes, como Montemar/UALA/
    Galicia) si la tabla wallets ya existía de antes y no la tiene."""
    existente = conn.execute("SELECT id FROM wallets WHERE id = 'new_wallet'").fetchone()
    if existente is not None:
        return
    w = next(w for w in DEFAULT_WALLETS if w.id == "new_wallet")
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


def _migrate_dia_habil(conn):
    """Agrega la columna `dia_habil` a egresos/ingresos si la tabla ya
    existía de una versión anterior del esquema (CREATE TABLE IF NOT EXISTS
    no altera tablas existentes)."""
    for tabla in ("egresos", "ingresos"):
        columnas = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabla})")}
        if "dia_habil" not in columnas:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN dia_habil INTEGER NOT NULL DEFAULT 0")


def _migrate_categoria(conn):
    """Agrega la columna `categoria` a egresos si la tabla ya existía."""
    columnas = {r["name"] for r in conn.execute("PRAGMA table_info(egresos)")}
    if "categoria" not in columnas:
        conn.execute("ALTER TABLE egresos ADD COLUMN categoria TEXT NOT NULL DEFAULT ''")


def _migrate_aumento_cuatrimestral(conn):
    """Agrega las columnas de aumento periódico (pct/inicio/cada cuántos
    meses) a egresos e ingresos si la tabla ya existía."""
    for tabla in ("egresos", "ingresos"):
        columnas = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabla})")}
        if "aumento_pct" not in columnas:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN aumento_pct REAL NOT NULL DEFAULT 0")
        if "aumento_inicio" not in columnas:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN aumento_inicio TEXT")
        if "aumento_periodo_meses" not in columnas:
            conn.execute(
                f"ALTER TABLE {tabla} ADD COLUMN aumento_periodo_meses INTEGER NOT NULL DEFAULT 4"
            )


def _migrate_moneda(conn):
    """Agrega la columna `moneda` a egresos e ingresos si la tabla ya existía."""
    for tabla in ("egresos", "ingresos"):
        columnas = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabla})")}
        if "moneda" not in columnas:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN moneda TEXT NOT NULL DEFAULT 'ARS'")


def _migrate_cuotas_y_aguinaldo(conn):
    """Agrega `hasta` (fin de un recurrente, ej. última cuota) y `aguinaldo`
    (ingresos que generan medio aguinaldo por semestre) si faltan."""
    for tabla in ("egresos", "ingresos"):
        columnas = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabla})")}
        if "hasta" not in columnas:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN hasta TEXT")
        if "aguinaldo" not in columnas:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN aguinaldo INTEGER NOT NULL DEFAULT 0")


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


def _seed_usuario_local(conn):
    conn.execute(
        "INSERT OR IGNORE INTO usuarios (id, nombre, creado) VALUES (?, 'Local', ?)",
        (USUARIO_LOCAL, datetime.now().isoformat(timespec="seconds")),
    )


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
    cursor = conn.execute(
        "UPDATE wallets SET tna = ?, capture_time = ?, payout_time = ?, activo = ?, "
        "monto_minimo = ?, monto_maximo = ? WHERE id = ?",
        (tna, capture_time, payout_time, int(activo), monto_minimo, monto_maximo, wallet_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_aportes(conn):
    rows = conn.execute("SELECT id, fecha, monto FROM aportes ORDER BY fecha, id").fetchall()
    return [(row["id"], date.fromisoformat(row["fecha"]), row["monto"]) for row in rows]


def add_aporte(conn, fecha, monto):
    conn.execute(
        "INSERT INTO aportes (fecha, monto) VALUES (?, ?)", (fecha.isoformat(), monto)
    )
    conn.commit()


def update_aporte(conn, aporte_id, fecha, monto):
    cursor = conn.execute(
        "UPDATE aportes SET fecha = ?, monto = ? WHERE id = ?",
        (fecha.isoformat(), monto, aporte_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_aporte(conn, aporte_id):
    conn.execute("DELETE FROM aportes WHERE id = ?", (aporte_id,))
    conn.commit()


def _row_to_movimiento(row, ajustes=(), cotizaciones=None, pagos=None) -> MovimientoRecurrente:
    columnas = row.keys()
    pagos = pagos or {}
    return MovimientoRecurrente(
        etiqueta=row["etiqueta"],
        monto=row["monto"],
        recurrente=bool(row["recurrente"]),
        fecha=date.fromisoformat(row["fecha"]) if row["fecha"] else None,
        dia_mes=row["dia_mes"],
        dia_habil=bool(row["dia_habil"]),
        categoria=row["categoria"] if "categoria" in columnas else "",
        aumento_pct=row["aumento_pct"] if "aumento_pct" in columnas else 0.0,
        aumento_inicio=(
            date.fromisoformat(row["aumento_inicio"])
            if "aumento_inicio" in columnas and row["aumento_inicio"]
            else None
        ),
        aumento_periodo_meses=row["aumento_periodo_meses"] if "aumento_periodo_meses" in columnas else 4,
        ajustes=tuple(ajustes),
        moneda=row["moneda"] if "moneda" in columnas else "ARS",
        cotizacion=(cotizaciones or {}).get(row["moneda"] if "moneda" in columnas else "ARS", 1.0),
        hasta=date.fromisoformat(row["hasta"]) if "hasta" in columnas and row["hasta"] else None,
        aguinaldo=row["aguinaldo"] if "aguinaldo" in columnas else 0,
        pagos=tuple(pagos.get("", ())),
        pagos_aguinaldo=tuple(pagos.get("aguinaldo", ())),
    )


def _get_movimientos(conn, tabla):
    rows = conn.execute(
        f"SELECT * FROM {tabla} ORDER BY recurrente DESC, fecha, dia_mes, id"
    ).fetchall()
    ajustes: dict[int, list] = {}
    for a in conn.execute(
        "SELECT movimiento_id, fecha, pct FROM ajustes WHERE tabla = ? ORDER BY fecha", (tabla,)
    ):
        ajustes.setdefault(a["movimiento_id"], []).append((date.fromisoformat(a["fecha"]), a["pct"]))
    # Sin cotización guardada, un movimiento en dólares vale 0 (no un dólar = un
    # peso): Configuración lo marca y no deja cargarlos sin cotización.
    cotizaciones = {"ARS": 1.0, "USD": 0.0}
    cotizaciones.update({r["moneda"]: r["valor"] for r in conn.execute("SELECT moneda, valor FROM cotizaciones")})
    pagos: dict[int, dict[str, list]] = {}
    for p in conn.execute(
        "SELECT movimiento_id, fecha, clave, monto FROM pagos WHERE tabla = ? ORDER BY fecha", (tabla,)
    ):
        pagos.setdefault(p["movimiento_id"], {}).setdefault(p["clave"], []).append(
            (date.fromisoformat(p["fecha"]), p["monto"])
        )
    return [
        (
            row["id"],
            _row_to_movimiento(row, ajustes.get(row["id"], ()), cotizaciones, pagos.get(row["id"])),
        )
        for row in rows
    ]


def get_cotizacion(conn, moneda):
    row = conn.execute("SELECT * FROM cotizaciones WHERE moneda = ?", (moneda,)).fetchone()
    if row is None:
        return None
    return {
        "moneda": row["moneda"],
        "valor": row["valor"],
        "fuente": row["fuente"],
        "publicado": datetime.fromisoformat(row["publicado"]),
        "actualizado": datetime.fromisoformat(row["actualizado"]),
    }


def guardar_cotizacion(conn, moneda, valor, fuente, publicado):
    conn.execute(
        "INSERT INTO cotizaciones (moneda, valor, fuente, publicado, actualizado) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (moneda) DO UPDATE SET valor = excluded.valor, fuente = excluded.fuente, "
        "publicado = excluded.publicado, actualizado = excluded.actualizado",
        (moneda, valor, fuente, publicado.isoformat(), datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def hay_movimientos_en(moneda, conn):
    return conn.execute(
        "SELECT 1 FROM egresos WHERE moneda = ? UNION ALL SELECT 1 FROM ingresos WHERE moneda = ? LIMIT 1",
        (moneda, moneda),
    ).fetchone() is not None


def _add_movimiento(
    conn, tabla, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil,
    aumento_pct=0.0, aumento_inicio=None, aumento_periodo_meses=4, moneda="ARS",
):
    conn.execute(
        f"INSERT INTO {tabla} (etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, "
        "aumento_pct, aumento_inicio, aumento_periodo_meses, moneda) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            etiqueta,
            monto,
            int(recurrente),
            fecha.isoformat() if fecha else None,
            dia_mes,
            int(dia_habil),
            aumento_pct,
            aumento_inicio.isoformat() if aumento_inicio else None,
            aumento_periodo_meses,
            moneda,
        ),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _update_movimiento(
    conn, tabla, movimiento_id, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil,
    aumento_pct=0.0, aumento_inicio=None, aumento_periodo_meses=4, moneda="ARS",
):
    cursor = conn.execute(
        f"UPDATE {tabla} SET etiqueta = ?, monto = ?, recurrente = ?, fecha = ?, dia_mes = ?, "
        "dia_habil = ?, aumento_pct = ?, aumento_inicio = ?, aumento_periodo_meses = ?, moneda = ? "
        "WHERE id = ?",
        (
            etiqueta,
            monto,
            int(recurrente),
            fecha.isoformat() if fecha else None,
            dia_mes,
            int(dia_habil),
            aumento_pct,
            aumento_inicio.isoformat() if aumento_inicio else None,
            aumento_periodo_meses,
            moneda,
            movimiento_id,
        ),
    )
    conn.commit()
    return cursor.rowcount > 0


def _delete_movimiento(conn, tabla, movimiento_id):
    conn.execute(f"DELETE FROM {tabla} WHERE id = ?", (movimiento_id,))
    conn.execute("DELETE FROM ajustes WHERE tabla = ? AND movimiento_id = ?", (tabla, movimiento_id))
    conn.execute("DELETE FROM pagos WHERE tabla = ? AND movimiento_id = ?", (tabla, movimiento_id))
    conn.commit()


def guardar_extras(conn, tabla, movimiento_id, hasta, aguinaldo):
    """Campos que se guardan aparte del alta/edición: fin del recurrente
    (`hasta`) y el modo de aguinaldo automático (solo ingresos)."""
    conn.execute(
        f"UPDATE {tabla} SET hasta = ?, aguinaldo = ? WHERE id = ?",
        (hasta.isoformat() if hasta else None, int(aguinaldo), movimiento_id),
    )
    conn.commit()


def guardar_pago(conn, tabla, movimiento_id, fecha, clave, monto):
    conn.execute(
        "INSERT INTO pagos (tabla, movimiento_id, fecha, clave, monto, registrado) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (tabla, movimiento_id, fecha, clave) DO UPDATE SET monto = excluded.monto, "
        "registrado = excluded.registrado",
        (tabla, movimiento_id, fecha.isoformat(), clave, monto, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def eliminar_pago(conn, tabla, movimiento_id, fecha, clave):
    conn.execute(
        "DELETE FROM pagos WHERE tabla = ? AND movimiento_id = ? AND fecha = ? AND clave = ?",
        (tabla, movimiento_id, fecha.isoformat(), clave),
    )
    conn.commit()


def get_controles_saldo(conn):
    rows = conn.execute("SELECT id, fecha, monto FROM controles_saldo ORDER BY fecha, id").fetchall()
    return [(row["id"], date.fromisoformat(row["fecha"]), row["monto"]) for row in rows]


def add_control_saldo(conn, fecha, monto):
    # Un control por día: el nuevo reemplaza al anterior de esa fecha.
    conn.execute("DELETE FROM controles_saldo WHERE fecha = ?", (fecha.isoformat(),))
    conn.execute(
        "INSERT INTO controles_saldo (fecha, monto, registrado) VALUES (?, ?, ?)",
        (fecha.isoformat(), monto, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def delete_control_saldo(conn, control_id):
    conn.execute("DELETE FROM controles_saldo WHERE id = ?", (control_id,))
    conn.commit()



def guardar_ajuste(conn, tabla, movimiento_id, fecha, pct):
    conn.execute(
        "INSERT INTO ajustes (tabla, movimiento_id, fecha, pct) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (tabla, movimiento_id, fecha) DO UPDATE SET pct = excluded.pct",
        (tabla, movimiento_id, fecha.isoformat(), pct),
    )
    conn.commit()


def eliminar_ajuste(conn, tabla, movimiento_id, fecha):
    conn.execute(
        "DELETE FROM ajustes WHERE tabla = ? AND movimiento_id = ? AND fecha = ?",
        (tabla, movimiento_id, fecha.isoformat()),
    )
    conn.commit()


def get_egresos(conn):
    return _get_movimientos(conn, "egresos")


def add_egreso(
    conn, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria,
    aumento_pct=0.0, aumento_inicio=None, aumento_periodo_meses=4, moneda="ARS",
):
    conn.execute(
        "INSERT INTO egresos (etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria, "
        "aumento_pct, aumento_inicio, aumento_periodo_meses, moneda) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            etiqueta,
            monto,
            int(recurrente),
            fecha.isoformat() if fecha else None,
            dia_mes,
            int(dia_habil),
            categoria,
            aumento_pct,
            aumento_inicio.isoformat() if aumento_inicio else None,
            aumento_periodo_meses,
            moneda,
        ),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def update_egreso(
    conn, egreso_id, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria,
    aumento_pct=0.0, aumento_inicio=None, aumento_periodo_meses=4, moneda="ARS",
):
    cursor = conn.execute(
        "UPDATE egresos SET etiqueta = ?, monto = ?, recurrente = ?, fecha = ?, dia_mes = ?, "
        "dia_habil = ?, categoria = ?, aumento_pct = ?, aumento_inicio = ?, "
        "aumento_periodo_meses = ?, moneda = ? WHERE id = ?",
        (
            etiqueta,
            monto,
            int(recurrente),
            fecha.isoformat() if fecha else None,
            dia_mes,
            int(dia_habil),
            categoria,
            aumento_pct,
            aumento_inicio.isoformat() if aumento_inicio else None,
            aumento_periodo_meses,
            moneda,
            egreso_id,
        ),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_egreso(conn, egreso_id):
    _delete_movimiento(conn, "egresos", egreso_id)


def get_ingresos(conn):
    return _get_movimientos(conn, "ingresos")


def add_ingreso(
    conn, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil,
    aumento_pct=0.0, aumento_inicio=None, aumento_periodo_meses=4, moneda="ARS",
):
    return _add_movimiento(
        conn, "ingresos", etiqueta, monto, recurrente, fecha, dia_mes, dia_habil,
        aumento_pct, aumento_inicio, aumento_periodo_meses, moneda,
    )


def update_ingreso(
    conn, ingreso_id, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil,
    aumento_pct=0.0, aumento_inicio=None, aumento_periodo_meses=4, moneda="ARS",
):
    return _update_movimiento(
        conn, "ingresos", ingreso_id, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil,
        aumento_pct, aumento_inicio, aumento_periodo_meses, moneda,
    )


def delete_ingreso(conn, ingreso_id):
    _delete_movimiento(conn, "ingresos", ingreso_id)


def get_inversiones(conn):
    rows = conn.execute("SELECT id, etiqueta, monto, tna FROM inversiones ORDER BY id").fetchall()
    return [(row["id"], row["etiqueta"], row["monto"], row["tna"]) for row in rows]


def add_inversion(conn, etiqueta, monto, tna):
    conn.execute(
        "INSERT INTO inversiones (etiqueta, monto, tna) VALUES (?, ?, ?)", (etiqueta, monto, tna)
    )
    conn.commit()


def update_inversion(conn, inversion_id, etiqueta, monto, tna):
    cursor = conn.execute(
        "UPDATE inversiones SET etiqueta = ?, monto = ?, tna = ? WHERE id = ?",
        (etiqueta, monto, tna, inversion_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_inversion(conn, inversion_id):
    conn.execute("DELETE FROM inversiones WHERE id = ?", (inversion_id,))
    conn.commit()
