"""Acceso a la base de Rutina. Todas las consultas filtran por `usuario`
(ver db.usuario_actual): cada persona ve y toca solo lo suyo."""

from datetime import date, datetime

from .logica import Habito, Tarea

SCHEMA = """
-- Hábitos: algo que se hace ciertos días de la semana, con una meta diaria
-- (1 = se tilda; más = se cuenta). `desde` es el primer día que se exige.
CREATE TABLE IF NOT EXISTS habitos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    nombre TEXT NOT NULL,
    icono TEXT NOT NULL DEFAULT '',
    dias TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
    meta INTEGER NOT NULL DEFAULT 1,
    unidad TEXT NOT NULL DEFAULT '',
    desde TEXT NOT NULL,
    activo INTEGER NOT NULL DEFAULT 1,
    orden INTEGER NOT NULL DEFAULT 0
);

-- Cuántas veces se hizo un hábito un día (sin fila = 0).
CREATE TABLE IF NOT EXISTS habitos_registro (
    habito_id INTEGER NOT NULL REFERENCES habitos(id) ON DELETE CASCADE,
    fecha TEXT NOT NULL,
    cantidad INTEGER NOT NULL,
    PRIMARY KEY (habito_id, fecha)
);

-- Tareas con fecha límite opcional; `hecha` es cuándo se completó.
CREATE TABLE IF NOT EXISTS tareas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    titulo TEXT NOT NULL,
    notas TEXT NOT NULL DEFAULT '',
    fecha TEXT,
    prioridad INTEGER NOT NULL DEFAULT 1,
    hecha TEXT,
    creada TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_habitos_usuario ON habitos (usuario_id);
CREATE INDEX IF NOT EXISTS idx_tareas_usuario ON tareas (usuario_id);
"""


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- Hábitos -----------------------------------------------------------------

def _habito(row) -> Habito:
    return Habito(
        id=row["id"],
        nombre=row["nombre"],
        dias=tuple(int(d) for d in row["dias"].split(",") if d != ""),
        desde=date.fromisoformat(row["desde"]),
        meta=row["meta"],
        unidad=row["unidad"],
        icono=row["icono"],
        activo=bool(row["activo"]),
    )


def get_habitos(conn, usuario: int, incluir_archivados: bool = False) -> list[Habito]:
    filtro = "" if incluir_archivados else " AND activo = 1"
    rows = conn.execute(
        f"SELECT * FROM habitos WHERE usuario_id = ?{filtro} ORDER BY activo DESC, orden, id", (usuario,)
    ).fetchall()
    return [_habito(r) for r in rows]


def get_habito(conn, usuario: int, habito_id: int) -> Habito | None:
    row = conn.execute("SELECT * FROM habitos WHERE id = ? AND usuario_id = ?", (habito_id, usuario)).fetchone()
    return _habito(row) if row else None


def add_habito(conn, usuario: int, nombre, icono, dias, meta, unidad, desde: date) -> int:
    orden = conn.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM habitos WHERE usuario_id = ?", (usuario,)).fetchone()[0]
    cursor = conn.execute(
        "INSERT INTO habitos (usuario_id, nombre, icono, dias, meta, unidad, desde, orden) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (usuario, nombre, icono, ",".join(map(str, dias)), meta, unidad, desde.isoformat(), orden),
    )
    conn.commit()
    return cursor.lastrowid


def update_habito(conn, usuario: int, habito_id: int, nombre, icono, dias, meta, unidad, desde: date) -> bool:
    cursor = conn.execute(
        "UPDATE habitos SET nombre = ?, icono = ?, dias = ?, meta = ?, unidad = ?, desde = ? WHERE id = ? AND usuario_id = ?",
        (nombre, icono, ",".join(map(str, dias)), meta, unidad, desde.isoformat(), habito_id, usuario),
    )
    conn.commit()
    return cursor.rowcount > 0


def set_habito_activo(conn, usuario: int, habito_id: int, activo: bool) -> bool:
    cursor = conn.execute(
        "UPDATE habitos SET activo = ? WHERE id = ? AND usuario_id = ?", (int(activo), habito_id, usuario)
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_habito(conn, usuario: int, habito_id: int) -> bool:
    if get_habito(conn, usuario, habito_id) is None:
        return False
    conn.execute("DELETE FROM habitos_registro WHERE habito_id = ?", (habito_id,))
    conn.execute("DELETE FROM habitos WHERE id = ? AND usuario_id = ?", (habito_id, usuario))
    conn.commit()
    return True


def get_cantidades(conn, usuario: int, desde: date, hasta: date) -> dict[tuple[int, date], int]:
    rows = conn.execute(
        "SELECT r.habito_id, r.fecha, r.cantidad FROM habitos_registro r "
        "JOIN habitos h ON h.id = r.habito_id "
        "WHERE h.usuario_id = ? AND r.fecha BETWEEN ? AND ?",
        (usuario, desde.isoformat(), hasta.isoformat()),
    ).fetchall()
    return {(r["habito_id"], date.fromisoformat(r["fecha"])): r["cantidad"] for r in rows}


def set_cantidad(conn, habito_id: int, fecha: date, cantidad: int) -> None:
    """Guarda cuántas veces se hizo (0 borra el registro). Quien llama ya
    verificó que el hábito es del usuario."""
    if cantidad <= 0:
        conn.execute("DELETE FROM habitos_registro WHERE habito_id = ? AND fecha = ?", (habito_id, fecha.isoformat()))
    else:
        conn.execute(
            "INSERT INTO habitos_registro (habito_id, fecha, cantidad) VALUES (?, ?, ?) "
            "ON CONFLICT (habito_id, fecha) DO UPDATE SET cantidad = excluded.cantidad",
            (habito_id, fecha.isoformat(), cantidad),
        )
    conn.commit()


# --- Tareas ------------------------------------------------------------------

def _tarea(row) -> Tarea:
    return Tarea(
        id=row["id"],
        titulo=row["titulo"],
        notas=row["notas"],
        fecha=date.fromisoformat(row["fecha"]) if row["fecha"] else None,
        prioridad=row["prioridad"],
        hecha=datetime.fromisoformat(row["hecha"]) if row["hecha"] else None,
        creada=datetime.fromisoformat(row["creada"]),
    )


def get_tareas(conn, usuario: int) -> list[Tarea]:
    rows = conn.execute("SELECT * FROM tareas WHERE usuario_id = ? ORDER BY id", (usuario,)).fetchall()
    return [_tarea(r) for r in rows]


def add_tarea(conn, usuario: int, titulo, notas, fecha: date | None, prioridad: int) -> int:
    cursor = conn.execute(
        "INSERT INTO tareas (usuario_id, titulo, notas, fecha, prioridad, creada) VALUES (?, ?, ?, ?, ?, ?)",
        (usuario, titulo, notas, fecha.isoformat() if fecha else None, prioridad, _ahora()),
    )
    conn.commit()
    return cursor.lastrowid


def update_tarea(conn, usuario: int, tarea_id: int, titulo, notas, fecha: date | None, prioridad: int) -> bool:
    cursor = conn.execute(
        "UPDATE tareas SET titulo = ?, notas = ?, fecha = ?, prioridad = ? WHERE id = ? AND usuario_id = ?",
        (titulo, notas, fecha.isoformat() if fecha else None, prioridad, tarea_id, usuario),
    )
    conn.commit()
    return cursor.rowcount > 0


def set_tarea_hecha(conn, usuario: int, tarea_id: int, hecha: bool) -> bool:
    cursor = conn.execute(
        "UPDATE tareas SET hecha = ? WHERE id = ? AND usuario_id = ?",
        (_ahora() if hecha else None, tarea_id, usuario),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_tarea(conn, usuario: int, tarea_id: int) -> bool:
    cursor = conn.execute("DELETE FROM tareas WHERE id = ? AND usuario_id = ?", (tarea_id, usuario))
    conn.commit()
    return cursor.rowcount > 0


def get_tarea(conn, usuario: int, tarea_id: int) -> Tarea | None:
    row = conn.execute("SELECT * FROM tareas WHERE id = ? AND usuario_id = ?", (tarea_id, usuario)).fetchone()
    return _tarea(row) if row else None
