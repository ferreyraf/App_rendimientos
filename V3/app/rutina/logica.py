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


# --- Agenda: bloques horarios ------------------------------------------------

def _minutos(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


@dataclass(frozen=True)
class Bloque:
    """Un rato del día reservado para algo. Se repite ciertos días de la
    semana (`dias`) o es de un solo día (`fecha`). Termina el mismo día."""
    id: int
    titulo: str
    inicio: str  # "HH:MM"
    fin: str
    dias: tuple[int, ...] = ()
    fecha: date | None = None
    aviso_min: int | None = None  # minutos antes de empezar (0 = a la hora; None = sin aviso)
    icono: str = ""
    notas: str = ""

    def aplica(self, dia: date) -> bool:
        return self.fecha == dia if self.fecha else dia.weekday() in self.dias

    @property
    def minuto_inicio(self) -> int:
        return _minutos(self.inicio)

    @property
    def minuto_fin(self) -> int:
        return _minutos(self.fin)

    @property
    def duracion(self) -> int:
        return self.minuto_fin - self.minuto_inicio

    @property
    def descripcion_cuando(self) -> str:
        if self.fecha:
            return self.fecha.strftime("%d/%m/%Y")
        return Habito(0, "", self.dias, date.min).descripcion_dias


def bloques_del_dia(bloques: list[Bloque], dia: date) -> list[Bloque]:
    return sorted((b for b in bloques if b.aplica(dia)), key=lambda b: (b.minuto_inicio, b.minuto_fin))


def ubicar_bloques(del_dia: list[Bloque]) -> list[tuple[Bloque, int, int]]:
    """(bloque, carril, carriles) para dibujar lado a lado los que se pisan:
    cada grupo de bloques encadenados por solapamiento reparte su ancho."""
    resultado, grupo, carriles_fin, fin_grupo = [], [], [], -1

    def cerrar():
        for bloque, carril in grupo:
            resultado.append((bloque, carril, len(carriles_fin)))

    for b in sorted(del_dia, key=lambda b: (b.minuto_inicio, b.minuto_fin)):
        if grupo and b.minuto_inicio >= fin_grupo:
            cerrar()
            grupo, carriles_fin = [], []
        carril = next((i for i, fin in enumerate(carriles_fin) if fin <= b.minuto_inicio), len(carriles_fin))
        if carril == len(carriles_fin):
            carriles_fin.append(b.minuto_fin)
        else:
            carriles_fin[carril] = b.minuto_fin
        grupo.append((b, carril))
        fin_grupo = max(fin_grupo, b.minuto_fin) if len(grupo) > 1 else b.minuto_fin
    if grupo:
        cerrar()
    return resultado


def estado_agenda(del_dia: list[Bloque], ahora: datetime) -> tuple[Bloque | None, Bloque | None]:
    """(bloque en curso, próximo en empezar) del día de `ahora`."""
    minuto = ahora.hour * 60 + ahora.minute
    en_curso = [b for b in del_dia if b.minuto_inicio <= minuto < b.minuto_fin]
    proximos = [b for b in del_dia if b.minuto_inicio > minuto]
    return (en_curso[-1] if en_curso else None), (proximos[0] if proximos else None)


def avisos_agenda(del_dia: list[Bloque], ahora: datetime, enviados: set) -> list[tuple[str, str, str]]:
    """Notificaciones que corresponden ahora: (clave, título, cuerpo). El
    aviso salta desde su momento hasta que empieza el bloque (o hasta 3 min
    después si es "a la hora"), una sola vez por clave."""
    avisos = []
    for b in del_dia:
        if b.aviso_min is None:
            continue
        empieza = datetime.combine(ahora.date(), datetime.strptime(b.inicio, "%H:%M").time())
        momento = empieza - timedelta(minutes=b.aviso_min)
        hasta = empieza if b.aviso_min else empieza + timedelta(minutes=3)
        if momento <= ahora < hasta:
            nombre = f"{b.icono} {b.titulo}".strip()
            titulo = f"Ahora: {nombre}" if b.aviso_min == 0 else f"En {b.aviso_min} min: {nombre}"
            cuerpo = f"De {b.inicio} a {b.fin}." + (f" {b.notas}" if b.notas else "")
            avisos.append((f"agenda-{b.id}-{ahora:%Y%m%d}-{b.inicio}", titulo, cuerpo))
    return [a for a in avisos if a[0] not in enviados]


# --- Alarmas -----------------------------------------------------------------

# Una alarma suena hasta que se la detiene o pospone; si nadie la atiende,
# se calla sola pasado este tiempo (como en el celular).
MINUTOS_SONANDO = 15


@dataclass(frozen=True)
class Alarma:
    """Suena a la `hora` ciertos días de la semana (`dias`) o una sola vez
    (`fecha`). `atendida`: la última vez que sonó y se detuvo o pospuso (lo
    anterior ya no vuelve a sonar). `pospuesta_hasta`: vuelve a sonar a esa
    hora."""
    id: int
    hora: str  # "HH:MM"
    etiqueta: str = ""
    dias: tuple[int, ...] = ()
    fecha: date | None = None
    sonido: str = "clasica"
    posponer_min: int = 5
    activa: bool = True
    atendida: datetime | None = None
    pospuesta_hasta: datetime | None = None

    @property
    def nombre(self) -> str:
        return self.etiqueta or "Alarma"

    @property
    def descripcion_cuando(self) -> str:
        if self.fecha:
            return f"Una vez, el {self.fecha:%d/%m/%Y}"
        return Habito(0, "", self.dias, date.min).descripcion_dias

    def _momento(self, dia: date) -> datetime:
        return datetime.combine(dia, datetime.strptime(self.hora, "%H:%M").time())

    def ultima(self, ahora: datetime) -> datetime | None:
        """La última vez que le tocó sonar (hasta `ahora` inclusive)."""
        if self.fecha:
            momento = self._momento(self.fecha)
            return momento if momento <= ahora else None
        for atras in range(8):
            dia = ahora.date() - timedelta(days=atras)
            if dia.weekday() in self.dias and self._momento(dia) <= ahora:
                return self._momento(dia)
        return None

    def proxima(self, ahora: datetime) -> datetime | None:
        """La próxima vez que va a sonar (después de `ahora`)."""
        if not self.activa:
            return None
        candidatas = []
        if self.pospuesta_hasta and self.pospuesta_hasta > ahora:
            candidatas.append(self.pospuesta_hasta)
        if self.fecha:
            momento = self._momento(self.fecha)
            if momento > ahora:
                candidatas.append(momento)
        else:
            for adelante in range(8):
                dia = ahora.date() + timedelta(days=adelante)
                if dia.weekday() in self.dias and self._momento(dia) > ahora:
                    candidatas.append(self._momento(dia))
                    break
        return min(candidatas, default=None)

    def sonando(self, ahora: datetime) -> datetime | None:
        """Desde cuándo está sonando, o None si no suena."""
        if not self.activa:
            return None
        ventana = timedelta(minutes=MINUTOS_SONANDO)
        if self.pospuesta_hasta and self.pospuesta_hasta <= ahora < self.pospuesta_hasta + ventana:
            return self.pospuesta_hasta
        ultima = self.ultima(ahora)
        if ultima and ahora < ultima + ventana and (self.atendida is None or ultima > self.atendida):
            return ultima
        return None


def alarmas_sonando(alarmas: list[Alarma], ahora: datetime) -> list[tuple[Alarma, datetime]]:
    sonando = [(a, desde) for a in alarmas if (desde := a.sonando(ahora))]
    return sorted(sonando, key=lambda par: par[1])


def proxima_alarma(alarmas: list[Alarma], ahora: datetime) -> tuple[Alarma, datetime] | None:
    proximas = [(a, cuando) for a in alarmas if (cuando := a.proxima(ahora))]
    return min(proximas, key=lambda par: par[1], default=None)


def falta(desde: datetime, hasta: datetime) -> str:
    """Cuánto falta, para mostrar: "en 7 h 20 min", "en 3 días"..."""
    minutos = max(0, -(-int((hasta - desde).total_seconds()) // 60))  # redondeado para arriba
    if minutos < 60:
        return f"en {minutos} min"
    horas, resto = divmod(minutos, 60)
    if horas < 24:
        return f"en {horas} h {resto} min" if resto else f"en {horas} h"
    dias = round(minutos / 1440)
    return f"en {dias} día{'s' if dias != 1 else ''}"
