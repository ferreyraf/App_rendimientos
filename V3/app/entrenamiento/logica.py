"""Reglas de Entrenamiento (gimnasio y cardio), sin base de datos ni Flask."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

GRUPOS = ("Pecho", "Espalda", "Piernas", "Hombros", "Bíceps", "Tríceps", "Core", "Cuerpo completo", "Otro")

# Para arrancar con un catálogo armado (botón en Rutinas y ejercicios).
EJERCICIOS_COMUNES = (
    ("Press de banca", "Pecho"), ("Press inclinado con mancuernas", "Pecho"), ("Aperturas", "Pecho"),
    ("Dominadas", "Espalda"), ("Remo con barra", "Espalda"), ("Jalón al pecho", "Espalda"),
    ("Sentadilla", "Piernas"), ("Peso muerto", "Piernas"), ("Prensa", "Piernas"), ("Zancadas", "Piernas"),
    ("Press militar", "Hombros"), ("Elevaciones laterales", "Hombros"),
    ("Curl de bíceps", "Bíceps"), ("Curl martillo", "Bíceps"),
    ("Extensión de tríceps", "Tríceps"), ("Fondos", "Tríceps"),
    ("Plancha", "Core"), ("Abdominales", "Core"),
)

TIPOS_CARDIO = {
    "correr": ("🏃", "Correr"),
    "caminar": ("🚶", "Caminar"),
    "bici": ("🚴", "Bici"),
    "nadar": ("🏊", "Nadar"),
    "otro": ("⚡", "Otro"),
}


# --- Gimnasio ----------------------------------------------------------------

@dataclass(frozen=True)
class Ejercicio:
    id: int
    nombre: str
    grupo: str = ""
    activo: bool = True


def rm_estimado(peso: float, reps: int) -> float:
    """Una repetición máxima estimada (fórmula de Epley): sirve para comparar
    series con distinto peso y repeticiones."""
    if reps <= 0 or peso <= 0:
        return 0.0
    return peso if reps == 1 else peso * (1 + reps / 30)


@dataclass(frozen=True)
class Serie:
    id: int
    ejercicio_id: int
    orden: int
    peso: float
    reps: int
    hecha: bool = False

    @property
    def volumen(self) -> float:
        return self.peso * self.reps if self.hecha else 0.0

    @property
    def rm(self) -> float:
        return rm_estimado(self.peso, self.reps) if self.hecha else 0.0


@dataclass(frozen=True)
class Sesion:
    id: int
    nombre: str
    inicio: datetime
    fin: datetime | None = None
    rutina_id: int | None = None
    notas: str = ""
    series: tuple[Serie, ...] = field(default_factory=tuple)

    @property
    def en_curso(self) -> bool:
        return self.fin is None

    @property
    def fecha(self) -> date:
        return self.inicio.date()

    @property
    def volumen(self) -> float:
        return sum(s.volumen for s in self.series)

    @property
    def series_hechas(self) -> int:
        return sum(1 for s in self.series if s.hecha)

    @property
    def minutos(self) -> int | None:
        return int((self.fin - self.inicio).total_seconds() // 60) if self.fin else None

    def ejercicios(self) -> list[int]:
        """Ids de ejercicios en el orden en que aparecen."""
        vistos = []
        for s in sorted(self.series, key=lambda s: s.orden):
            if s.ejercicio_id not in vistos:
                vistos.append(s.ejercicio_id)
        return vistos


def ultimas_series(sesiones: list[Sesion], ejercicio_id: int, antes_de: int | None = None) -> list[Serie]:
    """Series hechas del ejercicio en la última sesión terminada que lo tuvo
    (sin contar la sesión `antes_de`): con eso se precargan pesos y reps."""
    for sesion in sorted(sesiones, key=lambda s: s.inicio, reverse=True):
        if sesion.en_curso or sesion.id == antes_de:
            continue
        hechas = [s for s in sorted(sesion.series, key=lambda s: s.orden) if s.ejercicio_id == ejercicio_id and s.hecha]
        if hechas:
            return hechas
    return []


def records(sesiones: list[Sesion]) -> dict[int, dict]:
    """Por ejercicio: mejor peso, mejor 1RM estimado y más repeticiones en
    una serie (la marca que importa en los de peso corporal), con su fecha."""
    mejores: dict[int, dict] = {}
    for sesion in sorted(sesiones, key=lambda s: s.inicio):
        for s in sesion.series:
            if not s.hecha:
                continue
            m = mejores.get(s.ejercicio_id)
            if m is None:
                mejores[s.ejercicio_id] = {"peso": s.peso, "peso_fecha": sesion.fecha, "rm": s.rm, "rm_fecha": sesion.fecha,
                                           "reps": s.reps, "reps_fecha": sesion.fecha}
                continue
            if s.peso > m["peso"]:
                m["peso"], m["peso_fecha"] = s.peso, sesion.fecha
            if s.rm > m["rm"]:
                m["rm"], m["rm_fecha"] = s.rm, sesion.fecha
            if s.reps > m["reps"]:
                m["reps"], m["reps_fecha"] = s.reps, sesion.fecha
    return mejores


def records_por_sesion(sesiones: list[Sesion]) -> dict[int, list[int]]:
    """Por sesión: ejercicios en los que superó su mejor 1RM anterior (la
    primera vez que se hace un ejercicio no cuenta como récord)."""
    mejor: dict[int, float] = {}
    resultado: dict[int, list[int]] = {}
    for sesion in sorted(sesiones, key=lambda s: s.inicio):
        del_dia: dict[int, float] = {}
        for s in sesion.series:
            if s.hecha:
                del_dia[s.ejercicio_id] = max(del_dia.get(s.ejercicio_id, 0.0), s.rm)
        resultado[sesion.id] = [e for e, rm in del_dia.items() if e in mejor and rm > mejor[e] + 1e-9]
        for e, rm in del_dia.items():
            mejor[e] = max(mejor.get(e, 0.0), rm)
    return resultado


def progreso(sesiones: list[Sesion], ejercicio_id: int) -> list[dict]:
    """Por sesión terminada con ese ejercicio: fecha, mejor 1RM y peso máximo."""
    puntos = []
    for sesion in sorted(sesiones, key=lambda s: s.inicio):
        hechas = [s for s in sesion.series if s.ejercicio_id == ejercicio_id and s.hecha]
        if hechas and not sesion.en_curso:
            puntos.append({
                "fecha": sesion.fecha,
                "rm": round(max(s.rm for s in hechas), 1),
                "peso": max(s.peso for s in hechas),
                "reps": max(s.reps for s in hechas),
            })
    return puntos


def lunes_de(dia: date) -> date:
    return dia - timedelta(days=dia.weekday())


def semanas(hasta: date, n: int) -> list[date]:
    """Los lunes de las últimas `n` semanas (la de `hasta` incluida)."""
    ultimo = lunes_de(hasta)
    return [ultimo - timedelta(weeks=i) for i in range(n - 1, -1, -1)]


def por_semana(items, lunes: list[date], fecha, valor) -> list[float]:
    """Suma de `valor(item)` por semana, agrupando por `fecha(item)`."""
    totales = {l: 0.0 for l in lunes}
    for item in items:
        l = lunes_de(fecha(item))
        if l in totales:
            totales[l] += valor(item)
    return [totales[l] for l in lunes]


# --- Cardio ------------------------------------------------------------------

@dataclass(frozen=True)
class Actividad:
    id: int
    fecha: date
    tipo: str
    duracion_s: int
    distancia_km: float | None = None
    fc_media: int | None = None
    notas: str = ""

    @property
    def ritmo_s_km(self) -> float | None:
        """Segundos por kilómetro."""
        return self.duracion_s / self.distancia_km if self.distancia_km else None

    @property
    def velocidad_kmh(self) -> float | None:
        return self.distancia_km / (self.duracion_s / 3600) if self.distancia_km and self.duracion_s else None

    @property
    def emoji(self) -> str:
        return TIPOS_CARDIO.get(self.tipo, TIPOS_CARDIO["otro"])[0]

    @property
    def nombre_tipo(self) -> str:
        return TIPOS_CARDIO.get(self.tipo, TIPOS_CARDIO["otro"])[1]


def parse_duracion(texto: str) -> int:
    """Segundos a partir de "45" (minutos), "45:30" (min:seg) o "1:05:30"."""
    partes = texto.strip().split(":")
    if not texto.strip() or len(partes) > 3 or not all(p.strip().isdigit() for p in partes):
        raise ValueError("La duración va en minutos (45), min:seg (45:30) o h:min:seg (1:05:30).")
    numeros = [int(p) for p in partes]
    if len(numeros) == 1:
        return numeros[0] * 60
    if any(n >= 60 for n in numeros[1:]):
        raise ValueError("Revisá la duración: los minutos y segundos van de 0 a 59.")
    if len(numeros) == 2:
        return numeros[0] * 60 + numeros[1]
    return numeros[0] * 3600 + numeros[1] * 60 + numeros[2]


def formato_duracion(segundos: int | float | None) -> str:
    if segundos is None:
        return "—"
    segundos = int(round(segundos))
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def formato_ritmo(s_por_km: float | None) -> str:
    return f"{formato_duracion(s_por_km)} /km" if s_por_km else "—"
