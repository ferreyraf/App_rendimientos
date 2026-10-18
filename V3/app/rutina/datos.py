"""Acceso a la base de Rutina. Todas las consultas filtran por `usuario`
(ver db.usuario_actual): cada persona ve y toca solo lo suyo."""

from datetime import date, datetime

from .logica import Alarma, Bloque, Habito, Registro, Tarea

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

-- Cómo estuvo cada día: sueño de la noche anterior (horas "HH:MM"; las
-- horas dormidas se calculan), calidad y ánimo de 1 a 5, peso en kg y una nota.
CREATE TABLE IF NOT EXISTS registro_diario (
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    fecha TEXT NOT NULL,
    acostarse TEXT,
    levantarse TEXT,
    sueno_calidad INTEGER,
    animo INTEGER,
    peso REAL,
    nota TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (usuario_id, fecha)
);

-- Metas personales del registro (una fila por usuario).
CREATE TABLE IF NOT EXISTS rutina_objetivos (
    usuario_id INTEGER PRIMARY KEY REFERENCES usuarios(id),
    sueno_horas REAL NOT NULL DEFAULT 8,
    peso_objetivo REAL
);

-- Agenda: bloques horarios que se repiten ciertos días (`dias`) o son de un
-- solo día (`fecha`). `aviso_min`: minutos antes de empezar para avisar (0 =
-- a la hora, NULL = sin aviso).
CREATE TABLE IF NOT EXISTS agenda_bloques (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    titulo TEXT NOT NULL,
    icono TEXT NOT NULL DEFAULT '',
    inicio TEXT NOT NULL,
    fin TEXT NOT NULL,
    dias TEXT NOT NULL DEFAULT '',
    fecha TEXT,
    aviso_min INTEGER,
    notas TEXT NOT NULL DEFAULT ''
);

-- Alarmas: suenan a la `hora` ciertos días (`dias`) o una vez (`fecha`).
-- `atendida`: hasta qué momento ya se detuvo o pospuso (lo anterior no
-- vuelve a sonar); `pospuesta_hasta`: cuándo vuelve a sonar si se pospuso.
CREATE TABLE IF NOT EXISTS alarmas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    hora TEXT NOT NULL,
    etiqueta TEXT NOT NULL DEFAULT '',
    dias TEXT NOT NULL DEFAULT '',
    fecha TEXT,
    sonido TEXT NOT NULL DEFAULT 'clasica',
    posponer_min INTEGER NOT NULL DEFAULT 5,
    activa INTEGER NOT NULL DEFAULT 1,
    atendida TEXT,
    pospuesta_hasta TEXT
);

CREATE INDEX IF NOT EXISTS idx_alarmas_usuario ON alarmas (usuario_id);
CREATE INDEX IF NOT EXISTS idx_agenda_usuario ON agenda_bloques (usuario_id);
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


# --- Registro diario ---------------------------------------------------------

# Columnas que se pueden escribir desde un formulario (los nombres de columna
# del UPDATE salen de acá, nunca del pedido).
CAMPOS_REGISTRO = ("acostarse", "levantarse", "sueno_calidad", "animo", "peso", "nota")


def _registro(row) -> Registro:
    return Registro(
        fecha=date.fromisoformat(row["fecha"]),
        acostarse=row["acostarse"],
        levantarse=row["levantarse"],
        sueno_calidad=row["sueno_calidad"],
        animo=row["animo"],
        peso=row["peso"],
        nota=row["nota"],
    )


def get_registros(conn, usuario: int, desde: date, hasta: date) -> dict[date, Registro]:
    rows = conn.execute(
        "SELECT * FROM registro_diario WHERE usuario_id = ? AND fecha BETWEEN ? AND ? ORDER BY fecha",
        (usuario, desde.isoformat(), hasta.isoformat()),
    ).fetchall()
    return {date.fromisoformat(r["fecha"]): _registro(r) for r in rows}


def get_registro(conn, usuario: int, fecha: date) -> Registro | None:
    return get_registros(conn, usuario, fecha, fecha).get(fecha)


def guardar_registro(conn, usuario: int, fecha: date, campos: dict) -> None:
    """Actualiza solo los campos recibidos (un formulario corto no borra lo
    que no muestra). Si el día queda sin ningún dato, se borra la fila."""
    campos = {k: v for k, v in campos.items() if k in CAMPOS_REGISTRO}
    conn.execute(
        "INSERT OR IGNORE INTO registro_diario (usuario_id, fecha) VALUES (?, ?)", (usuario, fecha.isoformat())
    )
    if campos:
        asignaciones = ", ".join(f"{k} = ?" for k in campos)
        conn.execute(
            f"UPDATE registro_diario SET {asignaciones} WHERE usuario_id = ? AND fecha = ?",
            (*campos.values(), usuario, fecha.isoformat()),
        )
    conn.execute(
        "DELETE FROM registro_diario WHERE usuario_id = ? AND fecha = ? AND acostarse IS NULL "
        "AND levantarse IS NULL AND sueno_calidad IS NULL AND animo IS NULL AND peso IS NULL AND nota = ''",
        (usuario, fecha.isoformat()),
    )
    conn.commit()


def get_objetivos(conn, usuario: int) -> dict:
    row = conn.execute("SELECT sueno_horas, peso_objetivo FROM rutina_objetivos WHERE usuario_id = ?", (usuario,)).fetchone()
    return {"sueno_horas": row["sueno_horas"], "peso_objetivo": row["peso_objetivo"]} if row else {
        "sueno_horas": 8.0, "peso_objetivo": None}


def set_objetivos(conn, usuario: int, sueno_horas: float, peso_objetivo: float | None) -> None:
    conn.execute(
        "INSERT INTO rutina_objetivos (usuario_id, sueno_horas, peso_objetivo) VALUES (?, ?, ?) "
        "ON CONFLICT (usuario_id) DO UPDATE SET sueno_horas = excluded.sueno_horas, peso_objetivo = excluded.peso_objetivo",
        (usuario, sueno_horas, peso_objetivo),
    )
    conn.commit()


# --- Agenda ------------------------------------------------------------------

def _bloque(row) -> Bloque:
    return Bloque(
        id=row["id"],
        titulo=row["titulo"],
        inicio=row["inicio"],
        fin=row["fin"],
        dias=tuple(int(d) for d in row["dias"].split(",") if d != ""),
        fecha=date.fromisoformat(row["fecha"]) if row["fecha"] else None,
        aviso_min=row["aviso_min"],
        icono=row["icono"],
        notas=row["notas"],
    )


def get_bloques(conn, usuario: int) -> list[Bloque]:
    rows = conn.execute("SELECT * FROM agenda_bloques WHERE usuario_id = ? ORDER BY inicio, id", (usuario,)).fetchall()
    return [_bloque(r) for r in rows]


def _valores_bloque(titulo, icono, inicio, fin, dias, fecha, aviso_min, notas) -> tuple:
    return (titulo, icono, inicio, fin, ",".join(map(str, dias)), fecha.isoformat() if fecha else None, aviso_min, notas)


def add_bloque(conn, usuario: int, *valores) -> int:
    cursor = conn.execute(
        "INSERT INTO agenda_bloques (titulo, icono, inicio, fin, dias, fecha, aviso_min, notas, usuario_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*_valores_bloque(*valores), usuario),
    )
    conn.commit()
    return cursor.lastrowid


def update_bloque(conn, usuario: int, bloque_id: int, *valores) -> bool:
    cursor = conn.execute(
        "UPDATE agenda_bloques SET titulo = ?, icono = ?, inicio = ?, fin = ?, dias = ?, fecha = ?, aviso_min = ?, "
        "notas = ? WHERE id = ? AND usuario_id = ?",
        (*_valores_bloque(*valores), bloque_id, usuario),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_bloque(conn, usuario: int, bloque_id: int) -> bool:
    cursor = conn.execute("DELETE FROM agenda_bloques WHERE id = ? AND usuario_id = ?", (bloque_id, usuario))
    conn.commit()
    return cursor.rowcount > 0


# --- Alarmas -----------------------------------------------------------------

def _alarma(row) -> Alarma:
    return Alarma(
        id=row["id"],
        hora=row["hora"],
        etiqueta=row["etiqueta"],
        dias=tuple(int(d) for d in row["dias"].split(",") if d != ""),
        fecha=date.fromisoformat(row["fecha"]) if row["fecha"] else None,
        sonido=row["sonido"],
        posponer_min=row["posponer_min"],
        activa=bool(row["activa"]),
        atendida=datetime.fromisoformat(row["atendida"]) if row["atendida"] else None,
        pospuesta_hasta=datetime.fromisoformat(row["pospuesta_hasta"]) if row["pospuesta_hasta"] else None,
    )


def get_alarmas(conn, usuario: int) -> list[Alarma]:
    rows = conn.execute("SELECT * FROM alarmas WHERE usuario_id = ? ORDER BY hora, id", (usuario,)).fetchall()
    return [_alarma(r) for r in rows]


def get_alarma(conn, usuario: int, alarma_id: int) -> Alarma | None:
    row = conn.execute("SELECT * FROM alarmas WHERE id = ? AND usuario_id = ?", (alarma_id, usuario)).fetchone()
    return _alarma(row) if row else None


def _valores_alarma(hora, etiqueta, dias, fecha, sonido, posponer_min) -> tuple:
    return (hora, etiqueta, ",".join(map(str, dias)), fecha.isoformat() if fecha else None, sonido, posponer_min)


def add_alarma(conn, usuario: int, *valores) -> int:
    # Atendida "ahora": si la hora de hoy ya pasó, no tiene que sonar al crearla.
    cursor = conn.execute(
        "INSERT INTO alarmas (hora, etiqueta, dias, fecha, sonido, posponer_min, atendida, usuario_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (*_valores_alarma(*valores), _ahora(), usuario),
    )
    conn.commit()
    return cursor.lastrowid


def update_alarma(conn, usuario: int, alarma_id: int, *valores) -> bool:
    """Editarla la vuelve a activar desde ahora (y cancela si estaba pospuesta)."""
    cursor = conn.execute(
        "UPDATE alarmas SET hora = ?, etiqueta = ?, dias = ?, fecha = ?, sonido = ?, posponer_min = ?, "
        "activa = 1, atendida = ?, pospuesta_hasta = NULL WHERE id = ? AND usuario_id = ?",
        (*_valores_alarma(*valores), _ahora(), alarma_id, usuario),
    )
    conn.commit()
    return cursor.rowcount > 0


def set_alarma_activa(conn, usuario: int, alarma_id: int, activa: bool) -> bool:
    cursor = conn.execute(
        "UPDATE alarmas SET activa = ?, atendida = ?, pospuesta_hasta = NULL WHERE id = ? AND usuario_id = ?",
        (int(activa), _ahora(), alarma_id, usuario),
    )
    conn.commit()
    return cursor.rowcount > 0


def atender_alarma(conn, usuario: int, alarma_id: int, ahora: datetime, pospuesta_hasta: datetime | None,
                   desactivar: bool = False) -> bool:
    """Detiene (pospuesta_hasta None) o pospone lo que esté sonando. Las de
    una sola vez se desactivan al detenerlas."""
    cursor = conn.execute(
        "UPDATE alarmas SET atendida = ?, pospuesta_hasta = ?, activa = CASE WHEN ? THEN 0 ELSE activa END "
        "WHERE id = ? AND usuario_id = ?",
        (ahora.isoformat(timespec="seconds"), pospuesta_hasta.isoformat(timespec="seconds") if pospuesta_hasta else None,
         int(desactivar), alarma_id, usuario),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_alarma(conn, usuario: int, alarma_id: int) -> bool:
    cursor = conn.execute("DELETE FROM alarmas WHERE id = ? AND usuario_id = ?", (alarma_id, usuario))
    conn.commit()
    return cursor.rowcount > 0
