from dataclasses import dataclass


WEEKDAYS = (0, 1, 2, 3, 4)  # lunes a viernes
WEEKEND = (5, 6)  # sabado y domingo


@dataclass(frozen=True)
class Wallet:
    id: str
    name: str
    capture_time: str  # "HH:MM"
    active_weekdays: tuple[int, ...]
    default_tna: float  # porcentaje, ej. 18.44


DEFAULT_WALLETS = [
    Wallet("uala", "UALA", "09:00", WEEKDAYS, 18.44),
    Wallet("montemar", "Montemar Pay", "16:00", WEEKDAYS, 19.0),
    Wallet("mercadopago", "MercadoPago", "17:20", WEEKDAYS, 17.0),
    Wallet("galicia", "Galicia", "22:00", WEEKDAYS, 15.7),
    Wallet("nx", "Nx", "22:00", WEEKEND, 18.0),
]


def daily_yield(amount: float, tna_percent: float) -> float:
    return amount * (tna_percent / 100) / 365


def active_wallets_for_date(target_date, wallets: list[Wallet]) -> list[Wallet]:
    weekday = target_date.weekday()
    return [w for w in wallets if weekday in w.active_weekdays]
