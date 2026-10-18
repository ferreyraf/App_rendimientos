"""Acceso a la base de Entrenamiento. Todo filtra por `usuario` (ver
db.usuario_actual); las series y los ítems de rutina se alcanzan siempre a
través de una sesión o rutina del usuario."""

from datetime import date, datetime

from .logica import Actividad, Ejercicio, Serie, Sesion

SCHEMA = """
CREATE TABLE IF NOT EXISTS ejercicios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    nombre TEXT NOT NULL,
    grupo TEXT NOT NULL DEFAULT '',
    activo INTEGER NOT NULL DEFAULT 1
);

-- Rutina de gimnasio: qué ejercicios lleva (con series y reps objetivo) y
-- qué días de la semana toca (`dias` vacío = cuando quieras).
CREATE TABLE IF NOT EXISTS rutinas_gym (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    nombre TEXT NOT NULL,
    dias TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rutinas_gym_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rutina_id INTEGER NOT NULL REFERENCES rutinas_gym(id) ON DELETE CASCADE,
    ejercicio_id INTEGER NOT NULL REFERENCES ejercicios(id),
    orden INTEGER NOT NULL,
    series INTEGER NOT NULL DEFAULT 3,
    reps INTEGER NOT NULL DEFAULT 10
);

-- Un entrenamiento de gimnasio; `fin` NULL = en curso (uno por usuario).
CREATE TABLE IF NOT EXISTS sesiones_gym (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    rutina_id INTEGER,
    nombre TEXT NOT NULL,
    inicio TEXT NOT NULL,
    fin TEXT,
    notas TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS series_gym (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sesion_id INTEGER NOT NULL REFERENCES sesiones_gym(id) ON DELETE CASCADE,
    ejercicio_id INTEGER NOT NULL REFERENCES ejercicios(id),
    orden INTEGER NOT NULL,
    peso REAL NOT NULL DEFAULT 0,
    reps INTEGER NOT NULL DEFAULT 0,
    hecha INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS actividades_cardio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    fecha TEXT NOT NULL,
    tipo TEXT NOT NULL,
    duracion_s INTEGER NOT NULL,
    distancia_km REAL,
    fc_media INTEGER,
    notas TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ejercicios_usuario ON ejercicios (usuario_id);
CREATE INDEX IF NOT EXISTS idx_sesiones_usuario ON sesiones_gym (usuario_id, inicio);
CREATE INDEX IF NOT EXISTS idx_series_sesion ON series_gym (sesion_id);
CREATE INDEX IF NOT EXISTS idx_cardio_usuario ON actividades_cardio (usuario_id, fecha);
"""


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dias(texto: str) -> tuple[int, ...]:
    return tuple(int(d) for d in texto.split(",") if d != "")


# --- Ejercicios ----------------------------------------------------------------

def get_ejercicios(conn, usuario: int, incluir_archivados: bool = False) -> list[Ejercicio]:
    filtro = "" if incluir_archivados else " AND activo = 1"
    rows = conn.execute(
        f"SELECT * FROM ejercicios WHERE usuario_id = ?{filtro} ORDER BY activo DESC, grupo, nombre", (usuario,)
    ).fetchall()
    return [Ejercicio(r["id"], r["nombre"], r["grupo"], bool(r["activo"])) for r in rows]


def get_ejercicio(conn, usuario: int, ejercicio_id: int) -> Ejercicio | None:
    r = conn.execute("SELECT * FROM ejercicios WHERE id = ? AND usuario_id = ?", (ejercicio_id, usuario)).fetchone()
    return Ejercicio(r["id"], r["nombre"], r["grupo"], bool(r["activo"])) if r else None


def add_ejercicio(conn, usuario: int, nombre: str, grupo: str) -> int:
    cursor = conn.execute("INSERT INTO ejercicios (usuario_id, nombre, grupo) VALUES (?, ?, ?)", (usuario, nombre, grupo))
    conn.commit()
    return cursor.lastrowid


def update_ejercicio(conn, usuario: int, ejercicio_id: int, nombre: str, grupo: str) -> bool:
    cursor = conn.execute(
        "UPDATE ejercicios SET nombre = ?, grupo = ? WHERE id = ? AND usuario_id = ?", (nombre, grupo, ejercicio_id, usuario)
    )
    conn.commit()
    return cursor.rowcount > 0


def set_ejercicio_activo(conn, usuario: int, ejercicio_id: int, activo: bool) -> bool:
    cursor = conn.execute(
        "UPDATE ejercicios SET activo = ? WHERE id = ? AND usuario_id = ?", (int(activo), ejercicio_id, usuario)
    )
    conn.commit()
    return cursor.rowcount > 0


def ejercicio_en_uso(conn, ejercicio_id: int) -> bool:
    return bool(
        conn.execute("SELECT 1 FROM series_gym WHERE ejercicio_id = ? LIMIT 1", (ejercicio_id,)).fetchone()
        or conn.execute("SELECT 1 FROM rutinas_gym_items WHERE ejercicio_id = ? LIMIT 1", (ejercicio_id,)).fetchone()
    )


def delete_ejercicio(conn, usuario: int, ejercicio_id: int) -> bool:
    cursor = conn.execute("DELETE FROM ejercicios WHERE id = ? AND usuario_id = ?", (ejercicio_id, usuario))
    conn.commit()
    return cursor.rowcount > 0


# --- Rutinas -------------------------------------------------------------------

def get_rutinas(conn, usuario: int) -> list[dict]:
    """Rutinas con sus ítems: {id, nombre, dias, items: [{ejercicio_id, series, reps}]}."""
    rutinas = [
        {"id": r["id"], "nombre": r["nombre"], "dias": _dias(r["dias"]), "items": []}
        for r in conn.execute("SELECT * FROM rutinas_gym WHERE usuario_id = ? ORDER BY nombre", (usuario,))
    ]
    por_id = {r["id"]: r for r in rutinas}
    if por_id:
        marcas = ",".join("?" * len(por_id))
        for it in conn.execute(
            f"SELECT * FROM rutinas_gym_items WHERE rutina_id IN ({marcas}) ORDER BY orden", tuple(por_id)
        ):
            por_id[it["rutina_id"]]["items"].append(
                {"ejercicio_id": it["ejercicio_id"], "series": it["series"], "reps": it["reps"]}
            )
    return rutinas


def get_rutina(conn, usuario: int, rutina_id: int) -> dict | None:
    return next((r for r in get_rutinas(conn, usuario) if r["id"] == rutina_id), None)


def guardar_rutina(conn, usuario: int, rutina_id: int | None, nombre: str, dias, items) -> int | None:
    """Crea (rutina_id None) o reemplaza una rutina con sus ítems. None si la
    rutina a editar no es del usuario."""
    if rutina_id is None:
        rutina_id = conn.execute(
            "INSERT INTO rutinas_gym (usuario_id, nombre, dias) VALUES (?, ?, ?)",
            (usuario, nombre, ",".join(map(str, dias))),
        ).lastrowid
    else:
        cursor = conn.execute(
            "UPDATE rutinas_gym SET nombre = ?, dias = ? WHERE id = ? AND usuario_id = ?",
            (nombre, ",".join(map(str, dias)), rutina_id, usuario),
        )
        if cursor.rowcount == 0:
            return None
        conn.execute("DELETE FROM rutinas_gym_items WHERE rutina_id = ?", (rutina_id,))
    conn.executemany(
        "INSERT INTO rutinas_gym_items (rutina_id, ejercicio_id, orden, series, reps) VALUES (?, ?, ?, ?, ?)",
        [(rutina_id, e, i, s, r) for i, (e, s, r) in enumerate(items)],
    )
    conn.commit()
    return rutina_id


def delete_rutina(conn, usuario: int, rutina_id: int) -> bool:
    if conn.execute("SELECT 1 FROM rutinas_gym WHERE id = ? AND usuario_id = ?", (rutina_id, usuario)).fetchone() is None:
        return False
    conn.execute("DELETE FROM rutinas_gym_items WHERE rutina_id = ?", (rutina_id,))
    conn.execute("DELETE FROM rutinas_gym WHERE id = ?", (rutina_id,))
    conn.commit()
    return True


# --- Sesiones y series -----------------------------------------------------------

def get_sesiones(conn, usuario: int, desde: date | None = None) -> list[Sesion]:
    filtro, params = "", [usuario]
    if desde:
        filtro, params = " AND inicio >= ?", [usuario, desde.isoformat()]
    filas = conn.execute(f"SELECT * FROM sesiones_gym WHERE usuario_id = ?{filtro} ORDER BY inicio", params).fetchall()
    if not filas:
        return []
    marcas = ",".join("?" * len(filas))
    series: dict[int, list[Serie]] = {}
    for s in conn.execute(
        f"SELECT * FROM series_gym WHERE sesion_id IN ({marcas}) ORDER BY orden, id", tuple(f["id"] for f in filas)
    ):
        series.setdefault(s["sesion_id"], []).append(
            Serie(s["id"], s["ejercicio_id"], s["orden"], s["peso"], s["reps"], bool(s["hecha"]))
        )
    return [
        Sesion(
            id=f["id"],
            nombre=f["nombre"],
            inicio=datetime.fromisoformat(f["inicio"]),
            fin=datetime.fromisoformat(f["fin"]) if f["fin"] else None,
            rutina_id=f["rutina_id"],
            notas=f["notas"],
            series=tuple(series.get(f["id"], [])),
        )
        for f in filas
    ]


def get_sesion_en_curso(conn, usuario: int) -> Sesion | None:
    fila = conn.execute(
        "SELECT inicio FROM sesiones_gym WHERE usuario_id = ? AND fin IS NULL ORDER BY inicio DESC LIMIT 1", (usuario,)
    ).fetchone()
    if fila is None:
        return None
    return next((s for s in get_sesiones(conn, usuario, datetime.fromisoformat(fila["inicio"]).date()) if s.en_curso), None)


def crear_sesion(conn, usuario: int, nombre: str, rutina_id: int | None, series) -> int:
    """`series`: [(ejercicio_id, peso, reps)] en orden, todas sin hacer."""
    sesion_id = conn.execute(
        "INSERT INTO sesiones_gym (usuario_id, rutina_id, nombre, inicio) VALUES (?, ?, ?, ?)",
        (usuario, rutina_id, nombre, _ahora()),
    ).lastrowid
    conn.executemany(
        "INSERT INTO series_gym (sesion_id, ejercicio_id, orden, peso, reps) VALUES (?, ?, ?, ?, ?)",
        [(sesion_id, e, i, p, r) for i, (e, p, r) in enumerate(series)],
    )
    conn.commit()
    return sesion_id


def _sesion_del_usuario(conn, usuario: int, sesion_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM sesiones_gym WHERE id = ? AND usuario_id = ?", (sesion_id, usuario)
    ).fetchone() is not None


def agregar_serie(conn, usuario: int, sesion_id: int, ejercicio_id: int, peso: float, reps: int) -> bool:
    """Agrega una serie al final del ejercicio (o del entrenamiento, si el
    ejercicio todavía no estaba)."""
    if not _sesion_del_usuario(conn, usuario, sesion_id):
        return False
    ultimo = conn.execute(
        "SELECT MAX(orden) FROM series_gym WHERE sesion_id = ? AND ejercicio_id = ?", (sesion_id, ejercicio_id)
    ).fetchone()[0]
    if ultimo is None:
        orden = (conn.execute("SELECT MAX(orden) FROM series_gym WHERE sesion_id = ?", (sesion_id,)).fetchone()[0] or 0) + 1
    else:
        # Hace lugar justo después de la última serie de ese ejercicio.
        conn.execute("UPDATE series_gym SET orden = orden + 1 WHERE sesion_id = ? AND orden > ?", (sesion_id, ultimo))
        orden = ultimo + 1
    conn.execute(
        "INSERT INTO series_gym (sesion_id, ejercicio_id, orden, peso, reps) VALUES (?, ?, ?, ?, ?)",
        (sesion_id, ejercicio_id, orden, peso, reps),
    )
    conn.commit()
    return True


def _serie_del_usuario(conn, usuario: int, serie_id: int):
    return conn.execute(
        "SELECT s.* FROM series_gym s JOIN sesiones_gym g ON g.id = s.sesion_id WHERE s.id = ? AND g.usuario_id = ?",
        (serie_id, usuario),
    ).fetchone()


def actualizar_serie(conn, usuario: int, serie_id: int, peso: float, reps: int, hecha: bool) -> bool:
    if _serie_del_usuario(conn, usuario, serie_id) is None:
        return False
    conn.execute("UPDATE series_gym SET peso = ?, reps = ?, hecha = ? WHERE id = ?", (peso, reps, int(hecha), serie_id))
    conn.commit()
    return True


def eliminar_serie(conn, usuario: int, serie_id: int) -> bool:
    if _serie_del_usuario(conn, usuario, serie_id) is None:
        return False
    conn.execute("DELETE FROM series_gym WHERE id = ?", (serie_id,))
    conn.commit()
    return True


def terminar_sesion(conn, usuario: int, sesion_id: int, notas: str) -> bool:
    """Cierra el entrenamiento y descarta las series que no se hicieron."""
    cursor = conn.execute(
        "UPDATE sesiones_gym SET fin = ?, notas = ? WHERE id = ? AND usuario_id = ? AND fin IS NULL",
        (_ahora(), notas, sesion_id, usuario),
    )
    if cursor.rowcount:
        conn.execute("DELETE FROM series_gym WHERE sesion_id = ? AND hecha = 0", (sesion_id,))
    conn.commit()
    return cursor.rowcount > 0


def delete_sesion(conn, usuario: int, sesion_id: int) -> bool:
    if not _sesion_del_usuario(conn, usuario, sesion_id):
        return False
    conn.execute("DELETE FROM series_gym WHERE sesion_id = ?", (sesion_id,))
    conn.execute("DELETE FROM sesiones_gym WHERE id = ?", (sesion_id,))
    conn.commit()
    return True


# --- Cardio ----------------------------------------------------------------------

def get_actividades(conn, usuario: int, desde: date | None = None) -> list[Actividad]:
    filtro, params = "", [usuario]
    if desde:
        filtro, params = " AND fecha >= ?", [usuario, desde.isoformat()]
    return [
        Actividad(r["id"], date.fromisoformat(r["fecha"]), r["tipo"], r["duracion_s"], r["distancia_km"], r["fc_media"], r["notas"])
        for r in conn.execute(f"SELECT * FROM actividades_cardio WHERE usuario_id = ?{filtro} ORDER BY fecha, id", params)
    ]


def guardar_actividad(conn, usuario: int, actividad_id: int | None, fecha, tipo, duracion_s, distancia_km, fc_media, notas) -> bool:
    valores = (fecha.isoformat(), tipo, duracion_s, distancia_km, fc_media, notas)
    if actividad_id is None:
        conn.execute(
            "INSERT INTO actividades_cardio (fecha, tipo, duracion_s, distancia_km, fc_media, notas, usuario_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (*valores, usuario),
        )
        conn.commit()
        return True
    cursor = conn.execute(
        "UPDATE actividades_cardio SET fecha = ?, tipo = ?, duracion_s = ?, distancia_km = ?, fc_media = ?, notas = ? "
        "WHERE id = ? AND usuario_id = ?", (*valores, actividad_id, usuario),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_actividad(conn, usuario: int, actividad_id: int) -> bool:
    cursor = conn.execute("DELETE FROM actividades_cardio WHERE id = ? AND usuario_id = ?", (actividad_id, usuario))
    conn.commit()
    return cursor.rowcount > 0
