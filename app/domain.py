from __future__ import annotations

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


DEFAULT_WALLETS = [
    Wallet("uala", "UALA", "09:00", "19:00", WEEKDAYS, 18.44, bundles_weekend_payout=True),
    Wallet("montemar", "Montemar Pay", "16:00", "15:00", WEEKDAYS, 19.0, bundles_weekend_payout=True),
    Wallet("mercadopago", "MercadoPago", "17:20", "02:00", WEEKDAYS, 17.0, bundles_weekend_payout=True),
    Wallet("galicia", "Galicia", "22:00", "19:00", WEEKDAYS, 15.7, bundles_weekend_payout=True),
    Wallet("nx", "Nx", "22:00", "08:00", WEEKEND, 18.0, bundles_weekend_payout=False),
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


@dataclass
class DaySummary:
    date: date
    captures: list[CaptureEvent] = field(default_factory=list)
    rendimiento_generado: float = 0.0
    rendimiento_acreditado: float = 0.0
    capital_apertura: float = 0.0
    capital_cierre: float = 0.0


@dataclass
class _Evento:
    timestamp: datetime
    tipo: str  # "capture" | "payout"
    wallet_id: str
    wallet_name: str
    rendimiento: float = 0.0  # solo "payout"; lo completa la captura emparejada
    pago: "_Evento | None" = None  # solo "capture": referencia a su evento de pago
    dias_acumulados: int = 1  # solo "capture": cuántos días de rendimiento acumula su pago


def simulate(
    start_date: date, end_date: date, principal: float, wallets: list[Wallet]
) -> list[DaySummary]:
    """Reconstruye día a día el capital del rulo entre start_date y end_date (inclusive).

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
    eventos: list[_Evento] = []
    fecha = start_date
    while fecha <= end_date:
        weekday = fecha.weekday()
        for wallet in wallets:
            if weekday not in wallet.active_weekdays:
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

    eventos.sort(key=lambda e: (e.timestamp, e.tipo != "payout", e.wallet_id))

    wallets_by_id = {w.id: w for w in wallets}
    capital = principal
    summaries: dict[date, DaySummary] = {}

    def summary_for(d: date) -> DaySummary:
        if d not in summaries:
            summaries[d] = DaySummary(date=d, capital_apertura=capital)
        return summaries[d]

    for evento in eventos:
        dia = summary_for(evento.timestamp.date())
        if evento.tipo == "capture":
            wallet = wallets_by_id[evento.wallet_id]
            rendimiento = daily_yield(capital, wallet.default_tna)
            dia.captures.append(
                CaptureEvent(
                    wallet_id=wallet.id,
                    wallet_name=wallet.name,
                    timestamp=evento.timestamp,
                    monto_capturado=capital,
                    tna=wallet.default_tna,
                    rendimiento=rendimiento,
                    dias_acumulados=evento.dias_acumulados,
                )
            )
            dia.rendimiento_generado += rendimiento
            evento.pago.rendimiento = rendimiento * evento.dias_acumulados
        else:
            capital += evento.rendimiento
            dia.rendimiento_acreditado += evento.rendimiento
        dia.capital_cierre = capital

    return [summaries[d] for d in sorted(summaries) if start_date <= d <= end_date]
