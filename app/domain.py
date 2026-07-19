from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

WEEKDAYS = (0, 1, 2, 3, 4)  # lunes a viernes
WEEKEND = (5, 6)  # sabado y domingo


@dataclass(frozen=True)
class Wallet:
    id: str
    name: str
    capture_time: str  # "HH:MM"
    payout_time: str  # "HH:MM": acreditación del rendimiento del día anterior
    active_weekdays: tuple[int, ...]
    default_tna: float  # porcentaje, ej. 18.44
    bundles_weekend_payout: bool = False  # True: banca tradicional, no procesa pagos sábado/domingo
    activo: bool = True  # False: se excluye por completo de la simulación
    monto_minimo: float = 0.0  # por debajo de esto, no genera rendimiento
    monto_maximo: float | None = None  # por encima de esto, el excedente no genera rendimiento (None = sin techo)
    reparto_socio_id: str | None = None  # id de la billetera que recibe el excedente del split
    reparto_umbral: float | None = None  # monto que esta billetera se queda para sí en el split
    reparto_hora: str | None = None  # "HH:MM": hora del split (sábado) y del merge (lunes)


DEFAULT_WALLETS = [
    Wallet(
        "uala", "UALA", "09:00", "19:00", WEEKDAYS, 18.44,
        bundles_weekend_payout=True, monto_minimo=10_000,
    ),
    Wallet("montemar", "Montemar Pay", "16:00", "15:00", WEEKDAYS, 19.0, bundles_weekend_payout=True),
    Wallet("mercadopago", "MercadoPago", "17:20", "02:00", WEEKDAYS, 17.0, bundles_weekend_payout=True),
    Wallet("galicia", "Galicia", "22:00", "19:00", WEEKDAYS, 15.7, bundles_weekend_payout=True),
    Wallet(
        "nx", "Nx", "22:00", "08:00", WEEKEND, 18.0, bundles_weekend_payout=False,
        monto_maximo=1_000_000,
    ),
    Wallet("personal_pay", "Personal Pay", "22:00", "08:00", WEEKDAYS, 17.8, bundles_weekend_payout=True),
]


def daily_yield(amount: float, tna_percent: float) -> float:
    return amount * (tna_percent / 100) / 365


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


@dataclass
class CaptureEvent:
    wallet_id: str
    wallet_name: str
    timestamp: datetime
    monto_capturado: float
    tna: float
    rendimiento: float
    dias_acumulados: int = 1  # >1 cuando el pago se acumula por fin de semana (ej. viernes: 3)
    limitado: bool = False  # True si el piso/techo de la billetera alteró el cálculo


@dataclass(frozen=True)
class MovimientoRecurrente:
    """Definición de un egreso o ingreso etiquetado, cargada por el usuario.

    Si `recurrente` es True, se aplica todos los meses en `dia_mes`. Por
    default `dia_mes` es un día calendario (1-31; si el mes no lo tiene, se
    aplica el último día del mes). Si `dia_habil` es True, en cambio,
    `dia_mes` cuenta días hábiles (lunes a viernes) desde el 1ro del mes —
    útil para sueldos que se acreditan, por ejemplo, el "5to día hábil".

    Si `recurrente` es False, es puntual y se aplica una única vez en `fecha`.

    `categoria` es una clasificación libre y opcional (ej. "Vivienda",
    "Servicios") usada solo para agrupar en reportes — no afecta el cálculo.
    """

    etiqueta: str
    monto: float
    recurrente: bool
    fecha: date | None = None
    dia_mes: int | None = None
    dia_habil: bool = False
    categoria: str = ""


@dataclass
class Movimiento:
    etiqueta: str
    monto: float
    categoria: str = ""


@dataclass
class DaySummary:
    date: date
    captures: list[CaptureEvent] = field(default_factory=list)
    rendimiento_generado: float = 0.0
    rendimiento_acreditado: float = 0.0
    aporte_recibido: float = 0.0
    ingresos: list[Movimiento] = field(default_factory=list)
    egresos: list[Movimiento] = field(default_factory=list)
    capital_apertura: float = 0.0
    capital_cierre: float = 0.0

    @property
    def ingreso_recibido(self) -> float:
        return sum(m.monto for m in self.ingresos)

    @property
    def egreso_pagado(self) -> float:
        return sum(m.monto for m in self.egresos)


@dataclass
class _Evento:
    timestamp: datetime
    tipo: str  # "capture" | "payout" | "aporte" | "ingreso" | "egreso" | "split" | "merge"
    wallet_id: str = ""
    wallet_name: str = ""
    rendimiento: float = 0.0  # solo "payout"; lo completa la captura emparejada
    monto: float = 0.0  # solo "aporte"/"ingreso"/"egreso"
    etiqueta: str = ""  # solo "ingreso"/"egreso"
    pago: "_Evento | None" = None  # solo "capture": referencia a su evento de pago
    dias_acumulados: int = 1  # solo "capture": cuántos días de rendimiento acumula su pago
    ancla_id: str = ""  # solo "split"/"merge": billetera que se queda con el umbral
    socio_id: str = ""  # solo "split"/"merge": billetera que recibe el excedente
    umbral: float = 0.0  # solo "split"


def _n_esimo_dia_habil(anio: int, mes: int, n: int) -> date:
    """El n-ésimo día hábil (lunes a viernes) del mes."""
    dia, habiles = date(anio, mes, 1), 0
    while True:
        if dia.weekday() in WEEKDAYS:
            habiles += 1
            if habiles == n:
                return dia
        dia += timedelta(days=1)


def _fechas_recurrentes(dia_mes: int, dia_habil: bool, start: date, end: date) -> list[date]:
    """Fechas concretas (una por mes) en que cae un `dia_mes` recurrente
    entre `start` y `end` (inclusive). Si `dia_habil` es False (día
    calendario) y el mes no tiene ese día (ej. 31 en febrero), usa el
    último día de ese mes. Si `dia_habil` es True, `dia_mes` cuenta días
    hábiles desde el 1ro del mes (ej. 5 = 5to día hábil)."""
    fechas = []
    anio, mes = start.year, start.month
    while date(anio, mes, 1) <= end:
        if dia_habil:
            fecha_evento = _n_esimo_dia_habil(anio, mes, dia_mes)
        else:
            ultimo_dia = calendar.monthrange(anio, mes)[1]
            fecha_evento = date(anio, mes, min(dia_mes, ultimo_dia))
        if start <= fecha_evento <= end:
            fechas.append(fecha_evento)
        anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return fechas


def movimientos_del_mes(items: list[MovimientoRecurrente], anio: int, mes: int) -> list[Movimiento]:
    """Los egresos/ingresos (recurrentes resueltos a la fecha que les toca
    ese mes, más los puntuales que caen en el mes) — para reportes tipo
    "mes actual", no para la simulación día a día del rulo."""
    primer_dia = date(anio, mes, 1)
    ultimo_dia = date(anio, mes, calendar.monthrange(anio, mes)[1])
    resultado = []
    for item in items:
        if item.recurrente:
            ocurrencias = _fechas_recurrentes(item.dia_mes, item.dia_habil, primer_dia, ultimo_dia)
            resultado += [Movimiento(item.etiqueta, item.monto, item.categoria) for _ in ocurrencias]
        elif item.fecha is not None and primer_dia <= item.fecha <= ultimo_dia:
            resultado.append(Movimiento(item.etiqueta, item.monto, item.categoria))
    return resultado


def simulate(
    aportes: list[tuple[date, float]],
    egresos: list[MovimientoRecurrente],
    ingresos: list[MovimientoRecurrente],
    end_date: date,
    wallets: list[Wallet],
) -> list[DaySummary]:
    """Reconstruye día a día el capital del rulo hasta end_date (inclusive).

    `aportes` es la lista de inyecciones de capital (fecha, monto) — el primer
    aporte cronológico marca el arranque de la simulación, y aportes
    posteriores (ej. uno por mes) se suman al capital en su propia fecha.

    `egresos` e `ingresos` son listas de `MovimientoRecurrente` etiquetados:
    puntuales (una fecha) o recurrentes (todos los meses en `dia_mes`, ej.
    alquiler el día 10 o sueldo el día 5; si el mes no tiene ese día, cae el
    último día del mes).

    Cada captura genera un rendimiento pendiente que se acredita 24hs después
    (en el payout_time de la billetera). Si esa billetera no procesa pagos en
    fin de semana (`bundles_weekend_payout`) y el día de pago cae sábado o
    domingo, se pospone al lunes y se acumulan los días de por medio (ej. una
    captura del viernes acredita el lunes el rendimiento de viernes+sábado+
    domingo juntos, en vez de perderse el fin de semana). Nx no tiene este
    comportamiento porque procesa pagos todos los días, incluido el findesemana.

    Simplificación: usa la TNA y los horarios *actuales* de cada billetera para
    todo el rango histórico, no reconstruye cambios pasados de tasa u horario.
    """
    start_date = min(fecha for fecha, _ in aportes)
    wallets_by_id = {w.id: w for w in wallets}

    def _eventos_movimiento(items: list[MovimientoRecurrente], tipo: str) -> list[_Evento]:
        eventos = []
        for item in items:
            fechas = (
                _fechas_recurrentes(item.dia_mes, item.dia_habil, start_date, end_date)
                if item.recurrente
                else ([item.fecha] if start_date <= item.fecha <= end_date else [])
            )
            eventos += [
                _Evento(datetime.combine(f, time.min), tipo, monto=item.monto, etiqueta=item.etiqueta)
                for f in fechas
            ]
        return eventos

    eventos: list[_Evento] = (
        [
            _Evento(datetime.combine(fecha, time.min), "aporte", monto=monto)
            for fecha, monto in aportes
        ]
        + _eventos_movimiento(egresos, "egreso")
        + _eventos_movimiento(ingresos, "ingreso")
    )
    fecha = start_date
    while fecha <= end_date:
        weekday = fecha.weekday()

        if weekday == 5:  # sábado: posible split de fin de semana
            for wallet in wallets:
                socio = wallets_by_id.get(wallet.reparto_socio_id) if wallet.reparto_socio_id else None
                if wallet.activo and socio is not None and socio.activo:
                    split_ts = datetime.combine(fecha, _parse_hhmm(wallet.reparto_hora))
                    merge_ts = datetime.combine(
                        fecha + timedelta(days=2), _parse_hhmm(wallet.reparto_hora)
                    )
                    eventos.append(
                        _Evento(
                            split_ts, "split",
                            ancla_id=wallet.id, socio_id=socio.id, umbral=wallet.reparto_umbral,
                        )
                    )
                    eventos.append(
                        _Evento(merge_ts, "merge", ancla_id=wallet.id, socio_id=socio.id)
                    )

        for wallet in wallets:
            if not wallet.activo or weekday not in wallet.active_weekdays:
                continue
            captura_ts = datetime.combine(fecha, _parse_hhmm(wallet.capture_time))

            payout_dia = fecha + timedelta(days=1)
            dias_acumulados = 1
            if wallet.bundles_weekend_payout:
                while payout_dia.weekday() in WEEKEND:
                    payout_dia += timedelta(days=1)
                    dias_acumulados += 1
            payout_ts = datetime.combine(payout_dia, _parse_hhmm(wallet.payout_time))

            evento_pago = _Evento(payout_ts, "payout", wallet.id, wallet.name)
            evento_captura = _Evento(
                captura_ts,
                "capture",
                wallet.id,
                wallet.name,
                pago=evento_pago,
                dias_acumulados=dias_acumulados,
            )
            eventos.append(evento_captura)
            eventos.append(evento_pago)
        fecha += timedelta(days=1)

    orden_tipo = {"aporte": 0, "ingreso": 0, "egreso": 0, "payout": 1, "merge": 2, "split": 3, "capture": 4}
    eventos.sort(key=lambda e: (e.timestamp, orden_tipo[e.tipo], e.wallet_id))

    capital = 0.0
    en_reparto = False
    pozos: dict[str, float] = {}
    reparto_ancla_id: str | None = None
    reparto_umbral_actual: float | None = None
    summaries: dict[date, DaySummary] = {}

    def capital_total() -> float:
        return sum(pozos.values()) if en_reparto else capital

    def capital_de(wallet_id: str) -> float:
        if en_reparto and wallet_id in pozos:
            return pozos[wallet_id]
        return capital

    def sumar_capital(monto: float) -> None:
        # Un aporte/ingreso que cae en plena ventana de split no puede
        # sumarse al `capital` global: ese valor queda "dormido" hasta el
        # merge, que lo pisa por completo (pozos[ancla] + pozos[socio]) y el
        # monto se perdería. Se reparte igual que el split original: primero
        # completa el umbral del ancla, el resto va al socio.
        nonlocal capital
        if en_reparto:
            disponible_ancla = pozos.get(reparto_ancla_id, 0.0)
            espacio = max((reparto_umbral_actual or 0.0) - disponible_ancla, 0.0)
            a_ancla = min(monto, espacio)
            pozos[reparto_ancla_id] = disponible_ancla + a_ancla
            socio_id = next(k for k in pozos if k != reparto_ancla_id)
            pozos[socio_id] = pozos.get(socio_id, 0.0) + (monto - a_ancla)
        else:
            capital += monto

    def restar_capital(monto: float) -> None:
        # Simétrico: primero descuenta del pozo del socio (hasta dejarlo en
        # 0) y el remanente sale del ancla.
        nonlocal capital
        if en_reparto:
            socio_id = next(k for k in pozos if k != reparto_ancla_id)
            disponible_socio = pozos.get(socio_id, 0.0)
            del_socio = min(monto, disponible_socio)
            pozos[socio_id] = disponible_socio - del_socio
            pozos[reparto_ancla_id] = pozos.get(reparto_ancla_id, 0.0) - (monto - del_socio)
        else:
            capital -= monto

    def summary_for(d: date) -> DaySummary:
        if d not in summaries:
            summaries[d] = DaySummary(date=d, capital_apertura=capital_total())
        return summaries[d]

    for evento in eventos:
        dia = summary_for(evento.timestamp.date())
        if evento.tipo == "capture":
            wallet = wallets_by_id[evento.wallet_id]
            disponible = capital_de(evento.wallet_id)
            base = disponible
            limitado = False
            if disponible < wallet.monto_minimo:
                base = 0.0
                limitado = disponible > 0
            elif wallet.monto_maximo is not None and disponible > wallet.monto_maximo:
                base = wallet.monto_maximo
                limitado = True
            rendimiento = daily_yield(base, wallet.default_tna)
            dia.captures.append(
                CaptureEvent(
                    wallet_id=wallet.id,
                    wallet_name=wallet.name,
                    timestamp=evento.timestamp,
                    monto_capturado=disponible,
                    tna=wallet.default_tna,
                    rendimiento=rendimiento,
                    dias_acumulados=evento.dias_acumulados,
                    limitado=limitado,
                )
            )
            dia.rendimiento_generado += rendimiento
            evento.pago.rendimiento = rendimiento * evento.dias_acumulados
        elif evento.tipo == "payout":
            if en_reparto and evento.wallet_id in pozos:
                pozos[evento.wallet_id] += evento.rendimiento
            else:
                capital += evento.rendimiento
            dia.rendimiento_acreditado += evento.rendimiento
        elif evento.tipo == "aporte":
            sumar_capital(evento.monto)
            dia.aporte_recibido += evento.monto
        elif evento.tipo == "ingreso":
            sumar_capital(evento.monto)
            dia.ingresos.append(Movimiento(evento.etiqueta, evento.monto))
        elif evento.tipo == "egreso":
            restar_capital(evento.monto)
            dia.egresos.append(Movimiento(evento.etiqueta, evento.monto))
        elif evento.tipo == "split":
            monto_ancla = min(capital, evento.umbral)
            pozos = {evento.ancla_id: monto_ancla, evento.socio_id: capital - monto_ancla}
            en_reparto = True
            reparto_ancla_id = evento.ancla_id
            reparto_umbral_actual = evento.umbral
        else:  # "merge"
            capital = pozos.get(evento.ancla_id, 0.0) + pozos.get(evento.socio_id, 0.0)
            pozos = {}
            en_reparto = False
            reparto_ancla_id = None
            reparto_umbral_actual = None
        dia.capital_cierre = capital_total()

    return [summaries[d] for d in sorted(summaries) if start_date <= d <= end_date]


def proxima_captura(desde: datetime, wallets: list[Wallet]) -> tuple[datetime, Wallet] | None:
    """Encuentra la próxima captura (acción manual: mover el dinero) desde `desde`.

    Busca hasta 8 días adelante, suficiente para garantizar encontrar una
    billetera activa sin importar la combinación de días activos.
    """
    activos = [w for w in wallets if w.activo]
    if not activos:
        return None

    candidatos: list[tuple[datetime, Wallet]] = []
    for dias in range(8):
        fecha = desde.date() + timedelta(days=dias)
        weekday = fecha.weekday()
        for wallet in activos:
            if weekday not in wallet.active_weekdays:
                continue
            ts = datetime.combine(fecha, _parse_hhmm(wallet.capture_time))
            if ts >= desde:
                candidatos.append((ts, wallet))

    if not candidatos:
        return None
    return min(candidatos, key=lambda item: item[0])


def billetera_actual(desde: datetime, wallets: list[Wallet]) -> tuple[datetime, Wallet] | None:
    """Encuentra la última captura ya ocurrida antes de `desde` — dónde está la
    plata físicamente en este momento, según el rulo.

    Busca hasta 8 días atrás, simétrico a `proxima_captura`.
    """
    activos = [w for w in wallets if w.activo]
    if not activos:
        return None

    candidatos: list[tuple[datetime, Wallet]] = []
    for dias in range(8):
        fecha = desde.date() - timedelta(days=dias)
        weekday = fecha.weekday()
        for wallet in activos:
            if weekday not in wallet.active_weekdays:
                continue
            ts = datetime.combine(fecha, _parse_hhmm(wallet.capture_time))
            if ts <= desde:
                candidatos.append((ts, wallet))

    if not candidatos:
        return None
    return max(candidatos, key=lambda item: item[0])


def capital_simple(aportes: list[tuple[date, float]], tna_percent: float, hasta: date) -> float:
    """Capital resultante de dejar cada aporte quieto (interés simple, sin
    capitalizar) desde su propia fecha hasta `hasta`, a una TNA fija."""
    return sum(
        monto + monto * (tna_percent / 100) * ((hasta - fecha).days + 1) / 365
        for fecha, monto in aportes
    )


def tasa_efectiva_anual(principal_total: float, capital_final: float, dias: int) -> float:
    """TEA (%) implícita en haber convertido principal_total en capital_final
    a lo largo de `dias` días corridos."""
    if principal_total <= 0 or dias <= 0:
        return 0.0
    return ((capital_final / principal_total) ** (365 / dias) - 1) * 100


def proximo_vencimiento_impuestos(desde: date, dia_limite: int = 10) -> date:
    """Próxima fecha límite de pago: el `dia_limite` del mes actual si todavía
    no pasó, o del mes siguiente si ya pasó. Si cae sábado o domingo, se
    adelanta al viernes anterior."""
    if desde.day <= dia_limite:
        vencimiento = desde.replace(day=dia_limite)
    elif desde.month == 12:
        vencimiento = date(desde.year + 1, 1, dia_limite)
    else:
        vencimiento = date(desde.year, desde.month + 1, dia_limite)

    if vencimiento.weekday() in WEEKEND:
        vencimiento -= timedelta(days=vencimiento.weekday() - 4)
    return vencimiento
