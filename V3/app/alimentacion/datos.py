"""Acceso a la base de Alimentación. Todo filtra por `usuario` (ver
db.usuario_actual); los ítems de una comida frecuente se alcanzan siempre a
través de una frecuente del usuario."""

from datetime import date, datetime

from .logica import ALIMENTOS_BASICOS, Alimento, Entrada, Frecuente, Macros

SCHEMA = """
-- Macros cada 100 g; `gramos_unidad` + `unidad` permiten anotar por unidad
-- (ej. 2 huevos de 50 g).
CREATE TABLE IF NOT EXISTS alimentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    nombre TEXT NOT NULL,
    kcal REAL NOT NULL,
    proteinas REAL NOT NULL,
    carbohidratos REAL NOT NULL,
    grasas REAL NOT NULL,
    gramos_unidad REAL,
    unidad TEXT NOT NULL DEFAULT ''
);

-- Lo que se comió, con las macros ya calculadas (borrar o editar el
-- alimento no cambia el historial). `unidad` vacía = macros a mano.
CREATE TABLE IF NOT EXISTS comidas_registro (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    fecha TEXT NOT NULL,
    comida TEXT NOT NULL,
    alimento_id INTEGER REFERENCES alimentos(id) ON DELETE SET NULL,
    nombre TEXT NOT NULL,
    cantidad REAL,
    unidad TEXT NOT NULL DEFAULT '',
    gramos REAL,
    kcal REAL NOT NULL,
    proteinas REAL NOT NULL,
    carbohidratos REAL NOT NULL,
    grasas REAL NOT NULL,
    creado TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comidas_frecuentes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    nombre TEXT NOT NULL,
    comida TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comidas_frecuentes_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    frecuente_id INTEGER NOT NULL REFERENCES comidas_frecuentes(id) ON DELETE CASCADE,
    alimento_id INTEGER REFERENCES alimentos(id) ON DELETE SET NULL,
    nombre TEXT NOT NULL,
    cantidad REAL,
    unidad TEXT NOT NULL DEFAULT '',
    gramos REAL,
    kcal REAL NOT NULL,
    proteinas REAL NOT NULL,
    carbohidratos REAL NOT NULL,
    grasas REAL NOT NULL
);

-- Datos para calcular los objetivos y los objetivos diarios en sí (una
-- fila por usuario; los objetivos se pueden ajustar a mano).
CREATE TABLE IF NOT EXISTS alimentacion_perfil (
    usuario_id INTEGER PRIMARY KEY REFERENCES usuarios(id),
    sexo TEXT,
    nacimiento TEXT,
    altura_cm REAL,
    peso REAL,
    actividad TEXT,
    objetivo TEXT,
    kcal REAL,
    proteinas REAL,
    carbohidratos REAL,
    grasas REAL
);

CREATE INDEX IF NOT EXISTS idx_alimentos_usuario ON alimentos (usuario_id);
CREATE INDEX IF NOT EXISTS idx_comidas_usuario ON comidas_registro (usuario_id, fecha);
CREATE INDEX IF NOT EXISTS idx_frecuentes_items ON comidas_frecuentes_items (frecuente_id);
"""

COLUMNAS_MACROS = ("kcal", "proteinas", "carbohidratos", "grasas")


def crear_tablas(conn, usuario: int) -> None:
    """Crea las tablas y, la primera vez, carga los alimentos básicos."""
    nueva = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alimentos'").fetchone() is None
    conn.executescript(SCHEMA)
    if nueva:
        agregar_basicos(conn, usuario)


def _macros(row) -> Macros:
    return Macros(row["kcal"], row["proteinas"], row["carbohidratos"], row["grasas"])


# --- Alimentos -----------------------------------------------------------------

def _alimento(row) -> Alimento:
    return Alimento(row["id"], row["nombre"], _macros(row), row["gramos_unidad"], row["unidad"])


def get_alimentos(conn, usuario: int) -> list[Alimento]:
    rows = conn.execute("SELECT * FROM alimentos WHERE usuario_id = ? ORDER BY nombre COLLATE NOCASE", (usuario,))
    return [_alimento(r) for r in rows]


def get_alimento(conn, usuario: int, alimento_id: int) -> Alimento | None:
    r = conn.execute("SELECT * FROM alimentos WHERE id = ? AND usuario_id = ?", (alimento_id, usuario)).fetchone()
    return _alimento(r) if r else None


def guardar_alimento(conn, usuario: int, alimento_id: int | None, nombre: str, por_100g: Macros,
                     gramos_unidad: float | None, unidad: str) -> bool:
    valores = (nombre, por_100g.kcal, por_100g.proteinas, por_100g.carbohidratos, por_100g.grasas, gramos_unidad, unidad)
    if alimento_id is None:
        conn.execute(
            "INSERT INTO alimentos (nombre, kcal, proteinas, carbohidratos, grasas, gramos_unidad, unidad, usuario_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (*valores, usuario),
        )
        conn.commit()
        return True
    cursor = conn.execute(
        "UPDATE alimentos SET nombre = ?, kcal = ?, proteinas = ?, carbohidratos = ?, grasas = ?, gramos_unidad = ?, "
        "unidad = ? WHERE id = ? AND usuario_id = ?", (*valores, alimento_id, usuario),
    )
    conn.commit()
    return cursor.rowcount > 0


def agregar_basicos(conn, usuario: int) -> int:
    """Suma los alimentos básicos que todavía no estén (por nombre)."""
    existentes = {r["nombre"].lower() for r in conn.execute("SELECT nombre FROM alimentos WHERE usuario_id = ?", (usuario,))}
    nuevos = [a for a in ALIMENTOS_BASICOS if a[0].lower() not in existentes]
    conn.executemany(
        "INSERT INTO alimentos (usuario_id, nombre, kcal, proteinas, carbohidratos, grasas, gramos_unidad, unidad) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [(usuario, *a) for a in nuevos],
    )
    conn.commit()
    return len(nuevos)


def delete_alimento(conn, usuario: int, alimento_id: int) -> bool:
    """Lo ya anotado conserva sus macros; solo pierde el vínculo."""
    if get_alimento(conn, usuario, alimento_id) is None:
        return False
    conn.execute("UPDATE comidas_registro SET alimento_id = NULL WHERE alimento_id = ?", (alimento_id,))
    conn.execute("UPDATE comidas_frecuentes_items SET alimento_id = NULL WHERE alimento_id = ?", (alimento_id,))
    conn.execute("DELETE FROM alimentos WHERE id = ?", (alimento_id,))
    conn.commit()
    return True


# --- Lo que se comió -------------------------------------------------------------

def _entrada(row, fecha: date | None = None) -> Entrada:
    return Entrada(
        id=row["id"],
        fecha=fecha or date.fromisoformat(row["fecha"]),
        comida=row["comida"],
        nombre=row["nombre"],
        macros=_macros(row),
        alimento_id=row["alimento_id"],
        cantidad=row["cantidad"],
        unidad=row["unidad"],
        gramos=row["gramos"],
    )


def get_entradas(conn, usuario: int, desde: date, hasta: date) -> list[Entrada]:
    rows = conn.execute(
        "SELECT * FROM comidas_registro WHERE usuario_id = ? AND fecha BETWEEN ? AND ? ORDER BY fecha, id",
        (usuario, desde.isoformat(), hasta.isoformat()),
    )
    return [_entrada(r) for r in rows]


def guardar_entrada(conn, usuario: int, entrada_id: int | None, e: Entrada) -> bool:
    valores = (e.fecha.isoformat(), e.comida, e.alimento_id, e.nombre, e.cantidad, e.unidad, e.gramos,
               e.macros.kcal, e.macros.proteinas, e.macros.carbohidratos, e.macros.grasas)
    if entrada_id is None:
        conn.execute(
            "INSERT INTO comidas_registro (fecha, comida, alimento_id, nombre, cantidad, unidad, gramos, kcal, proteinas, "
            "carbohidratos, grasas, usuario_id, creado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*valores, usuario, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return True
    cursor = conn.execute(
        "UPDATE comidas_registro SET fecha = ?, comida = ?, alimento_id = ?, nombre = ?, cantidad = ?, unidad = ?, "
        "gramos = ?, kcal = ?, proteinas = ?, carbohidratos = ?, grasas = ? WHERE id = ? AND usuario_id = ?",
        (*valores, entrada_id, usuario),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_entrada(conn, usuario: int, entrada_id: int) -> bool:
    cursor = conn.execute("DELETE FROM comidas_registro WHERE id = ? AND usuario_id = ?", (entrada_id, usuario))
    conn.commit()
    return cursor.rowcount > 0


# --- Comidas frecuentes ------------------------------------------------------------

def get_frecuentes(conn, usuario: int) -> list[Frecuente]:
    filas = conn.execute(
        "SELECT * FROM comidas_frecuentes WHERE usuario_id = ? ORDER BY nombre COLLATE NOCASE", (usuario,)
    ).fetchall()
    if not filas:
        return []
    marcas = ",".join("?" * len(filas))
    items: dict[int, list[Entrada]] = {}
    for r in conn.execute(
        f"SELECT *, NULL AS fecha, '' AS comida FROM comidas_frecuentes_items WHERE frecuente_id IN ({marcas}) ORDER BY id",
        tuple(f["id"] for f in filas),
    ):
        items.setdefault(r["frecuente_id"], []).append(_entrada(r, date.min))
    return [Frecuente(f["id"], f["nombre"], f["comida"], tuple(items.get(f["id"], []))) for f in filas]


def crear_frecuente(conn, usuario: int, nombre: str, comida: str, entradas: list[Entrada]) -> int:
    frecuente_id = conn.execute(
        "INSERT INTO comidas_frecuentes (usuario_id, nombre, comida) VALUES (?, ?, ?)", (usuario, nombre, comida)
    ).lastrowid
    conn.executemany(
        "INSERT INTO comidas_frecuentes_items (frecuente_id, alimento_id, nombre, cantidad, unidad, gramos, kcal, "
        "proteinas, carbohidratos, grasas) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(frecuente_id, e.alimento_id, e.nombre, e.cantidad, e.unidad, e.gramos,
          e.macros.kcal, e.macros.proteinas, e.macros.carbohidratos, e.macros.grasas) for e in entradas],
    )
    conn.commit()
    return frecuente_id


def delete_frecuente(conn, usuario: int, frecuente_id: int) -> bool:
    if conn.execute("SELECT 1 FROM comidas_frecuentes WHERE id = ? AND usuario_id = ?", (frecuente_id, usuario)).fetchone() is None:
        return False
    conn.execute("DELETE FROM comidas_frecuentes_items WHERE frecuente_id = ?", (frecuente_id,))
    conn.execute("DELETE FROM comidas_frecuentes WHERE id = ?", (frecuente_id,))
    conn.commit()
    return True


# --- Perfil y objetivos --------------------------------------------------------------

def get_perfil(conn, usuario: int) -> dict:
    row = conn.execute("SELECT * FROM alimentacion_perfil WHERE usuario_id = ?", (usuario,)).fetchone()
    perfil = dict(row) if row else {
        "sexo": None, "nacimiento": None, "altura_cm": None, "peso": None, "actividad": None, "objetivo": None,
        "kcal": None, "proteinas": None, "carbohidratos": None, "grasas": None,
    }
    perfil["nacimiento"] = date.fromisoformat(perfil["nacimiento"]) if perfil["nacimiento"] else None
    return perfil


def guardar_perfil(conn, usuario: int, campos: dict) -> None:
    """Actualiza solo los `campos` dados (datos personales u objetivos)."""
    valores = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in campos.items()}
    columnas = ", ".join(valores)
    conn.execute(
        f"INSERT INTO alimentacion_perfil (usuario_id, {columnas}) VALUES (?, {', '.join('?' * len(valores))}) "
        f"ON CONFLICT (usuario_id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in valores)}",
        (usuario, *valores.values()),
    )
    conn.commit()


def ultimo_peso(conn, usuario: int) -> tuple[float, date] | None:
    """El último peso cargado en el registro diario (Rutina)."""
    row = conn.execute(
        "SELECT peso, fecha FROM registro_diario WHERE usuario_id = ? AND peso IS NOT NULL ORDER BY fecha DESC LIMIT 1",
        (usuario,),
    ).fetchone()
    return (row["peso"], date.fromisoformat(row["fecha"])) if row else None
