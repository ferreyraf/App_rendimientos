"""Reglas de Rutina (hábitos y tareas), sin base de datos ni Flask.

Un hábito se hace ciertos días de la semana y tiene una meta diaria (1 =
se tilda; más de 1 = se cuenta, ej. 8 vasos de agua). `cantidades` es
siempre un dict {(habito_id, fecha): veces hechas ese día}.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

DIAS_CORTOS = ("L", "M", "M", "J", "V", "S", "D")
TODOS_LOS_DIAS = (0, 1, 2, 3, 4, 5, 6)

PRIORIDADES = {0: "Baja", 1: "Normal", 2: "Alta"}


@dataclass(frozen=True)
class Habito:
    id: int
    nombre: str
    dias: tuple[int, ...]  # días de la semana en que aplica (0 = lunes)
    desde: date  # primer día que cuenta (no se exige nada antes)
    meta: int = 1
    unidad: str = ""
    icono: str = ""
    activo: bool = True

    def aplica(self, fecha: date) -> bool:
        return fecha >= self.desde and fecha.weekday() in self.dias

    def hecho(self, cantidades: dict, fecha: date) -> bool:
        return cantidades.get((self.id, fecha), 0) >= self.meta

    @property
    def descripcion_dias(self) -> str:
        if tuple(sorted(self.dias)) == TODOS_LOS_DIAS:
            return "Todos los días"
        if tuple(sorted(self.dias)) == (0, 1, 2, 3, 4):
            return "Lunes a viernes"
        if tuple(sorted(self.dias)) == (5, 6):
            return "Fines de semana"
        return " ".join(DIAS_CORTOS[d] for d in sorted(self.dias))


def racha(habito: Habito, cantidades: dict, hoy: date) -> int:
    """Días seguidos (de los que aplica) cumplidos hasta hoy. Si hoy aplica
    y todavía no se hizo, no corta la racha: se cuenta desde ayer."""
    dia = hoy
    if habito.aplica(dia) and not habito.hecho(cantidades, dia):
        dia -= timedelta(days=1)
    n = 0
    while dia >= habito.desde:
        if habito.aplica(dia):
            if not habito.hecho(cantidades, dia):
                break
            n += 1
        dia -= timedelta(days=1)
    return n


def mejor_racha(habito: Habito, cantidades: dict, hoy: date) -> int:
    mejor = actual = 0
    dia = habito.desde
    while dia <= hoy:
        if habito.aplica(dia):
            if habito.hecho(cantidades, dia):
                actual += 1
                mejor = max(mejor, actual)
            elif dia < hoy:  # hoy sin hacer todavía no corta
                actual = 0
        dia += timedelta(days=1)
    return mejor


def cumplimiento(habitos: list[Habito], cantidades: dict, desde: date, hasta: date, hoy: date) -> tuple[int, int]:
    """(cumplidos, exigidos) entre `desde` y `hasta`, sumando todos los
    hábitos. Hoy solo cuenta si ya se hizo: el día todavía no terminó."""
    hechos = exigidos = 0
    dia = desde
    while dia <= hasta:
        for h in habitos:
            if not h.aplica(dia):
                continue
            hecho = h.hecho(cantidades, dia)
            if dia == hoy and not hecho:
                continue
            exigidos += 1
            hechos += hecho
        dia += timedelta(days=1)
    return hechos, exigidos


def porcentaje(hechos: int, exigidos: int) -> int | None:
    return round(hechos * 100 / exigidos) if exigidos else None


def progreso_del_dia(habitos: list[Habito], cantidades: dict, fecha: date) -> tuple[int, int]:
    """(hechos, total) de los hábitos que aplican ese día."""
    del_dia = [h for h in habitos if h.aplica(fecha)]
    return sum(h.hecho(cantidades, fecha) for h in del_dia), len(del_dia)


@dataclass(frozen=True)
class Tarea:
    id: int
    titulo: str
    notas: str
    fecha: date | None  # fecha límite (None = sin fecha)
    prioridad: int
    hecha: datetime | None
    creada: datetime

    @property
    def pendiente(self) -> bool:
        return self.hecha is None

    def atrasada(self, hoy: date) -> bool:
        return self.pendiente and self.fecha is not None and self.fecha < hoy


def clasificar_tareas(tareas: list[Tarea], hoy: date, dias_completadas: int = 30) -> dict[str, list[Tarea]]:
    """Tareas agrupadas como se muestran: para hoy (incluye atrasadas),
    próximas, sin fecha y completadas recientes."""
    pendientes = [t for t in tareas if t.pendiente]
    por_fecha = lambda t: (t.fecha or date.max, -t.prioridad, t.creada)
    limite = datetime.combine(hoy - timedelta(days=dias_completadas), datetime.min.time())
    return {
        "hoy": sorted((t for t in pendientes if t.fecha is not None and t.fecha <= hoy), key=por_fecha),
        "proximas": sorted((t for t in pendientes if t.fecha is not None and t.fecha > hoy), key=por_fecha),
        "sin_fecha": sorted((t for t in pendientes if t.fecha is None), key=lambda t: (-t.prioridad, t.creada)),
        "completadas": sorted((t for t in tareas if t.hecha and t.hecha >= limite), key=lambda t: t.hecha, reverse=True),
    }


# --- Registro diario: sueño, ánimo y peso -------------------------------------

ANIMOS = {1: ("😞", "Muy mal"), 2: ("😕", "Mal"), 3: ("😐", "Normal"), 4: ("🙂", "Bien"), 5: ("😄", "Muy bien")}
CALIDADES = {1: "Pésimo", 2: "Malo", 3: "Regular", 4: "Bueno", 5: "Excelente"}


def horas_de_sueno(acostarse: str, levantarse: str) -> float:
    """Horas entre dos horas "HH:MM"; si levantarse es menor, cruzó la medianoche."""
    a_h, a_m = (int(x) for x in acostarse.split(":"))
    l_h, l_m = (int(x) for x in levantarse.split(":"))
    minutos = ((l_h * 60 + l_m) - (a_h * 60 + a_m)) % (24 * 60)
    return minutos / 60


@dataclass(frozen=True)
class Registro:
    """Cómo estuvo un día. El sueño es el de la noche anterior (te acostaste
    el día antes y te levantaste ese día)."""
    fecha: date
    acostarse: str | None = None
    levantarse: str | None = None
    sueno_calidad: int | None = None
    animo: int | None = None
    peso: float | None = None
    nota: str = ""

    @property
    def horas_sueno(self) -> float | None:
        if self.acostarse and self.levantarse:
            return horas_de_sueno(self.acostarse, self.levantarse)
        return None

    @property
    def emoji(self) -> str:
        return ANIMOS[self.animo][0] if self.animo in ANIMOS else ""


def promedio(valores) -> float | None:
    valores = [v for v in valores if v is not None]
    return sum(valores) / len(valores) if valores else None


def media_movil(serie: dict[date, float], dias: list[date], ventana: int = 7) -> list[float | None]:
    """Para cada día, el promedio de los valores cargados en los `ventana`
    días que terminan ese día (None si no hay ninguno)."""
    return [
        promedio(serie.get(d - timedelta(days=i)) for i in range(ventana))
        for d in dias
    ]


def valor_hace(serie: dict[date, float], fecha: date, tolerancia: int = 7) -> float | None:
    """El último valor cargado en `fecha` o hasta `tolerancia` días antes."""
    for i in range(tolerancia + 1):
        if (fecha - timedelta(days=i)) in serie:
            return serie[fecha - timedelta(days=i)]
    return None
