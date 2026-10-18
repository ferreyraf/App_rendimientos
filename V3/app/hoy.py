"""Pantalla de inicio: el día completo de un vistazo.

Cada sección de la app (Finanzas; más adelante Rutina y Entrenamiento)
aporta lo suyo con una función registrada en APORTES que devuelve un
AporteHoy: indicadores para la fila de arriba, paneles de "tu día" (lo que
hay que hacer hoy) y paneles de "cómo venís" (estado general). La pantalla
solo los junta y los acomoda en carruseles; sumar una sección no la toca.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, url_for
from markupsafe import Markup

from . import db, formato_pesos
from .domain import proximo_vencimiento_impuestos, proximos_movimientos, simulate, sumar_meses
from .routes import (
    DIAS,
    MESES,
    _ajustes_saldo,
    _alertas,
    _aportes_pares,
    _egresos,
    _ingresos,
    _ocurrencias_mes,
    _wallet_from_row,
    estado_rulo,
)

bp = Blueprint("hoy", __name__)


@dataclass
class Indicador:
    etiqueta: str
    valor: str
    nota: str = ""
    clase: str = ""  # "positivo" / "negativo" para colorear el valor
    url: str | None = None


@dataclass
class Panel:
    titulo: str
    html: Markup


@dataclass
class AporteHoy:
    indicadores: list[Indicador] = field(default_factory=list)
    paneles_dia: list[Panel] = field(default_factory=list)
    paneles_estado: list[Panel] = field(default_factory=list)
    alertas: list[dict] = field(default_factory=list)
    chips: list[str] = field(default_factory=list)  # avisos cortos junto al saludo


def panel(titulo: str, plantilla: str, **contexto) -> Panel:
    return Panel(titulo, Markup(render_template(plantilla, **contexto)))


def aporte_finanzas(conn, ahora: datetime) -> AporteHoy:
    hoy = ahora.date()
    aportes = _aportes_pares(conn)
    if not aportes:
        return AporteHoy(
            indicadores=[Indicador("Finanzas", "Sin datos", "Cargá tu primer aporte", url=url_for("main.configuracion", _anchor="aportes"))],
            paneles_estado=[panel("Finanzas", "hoy/_finanzas_vacio.html")],
        )

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    ajustes = _ajustes_saldo(conn, aportes, egresos, ingresos, wallets)

    # Misma ventana que el Resumen, para que las alertas coincidan.
    horizonte = sumar_meses(hoy, 9) - timedelta(days=1)
    summaries = simulate(aportes, egresos, ingresos, horizonte, wallets, ajustes)
    capital_por_dia = {s.date: s.capital_cierre for s in summaries}
    summary = next((s for s in summaries if s.date == hoy), None)
    capital_hoy = summary.capital_cierre if summary else sum(m for _, m in aportes)
    actual, proxima = estado_rulo(ahora, wallets)
    vencimiento = proximo_vencimiento_impuestos(hoy)

    # Lo de hoy más lo atrasado del mes (desde el arranque del rulo) sin marcar.
    inicio = min(f for f, _ in aportes)
    del_mes = _ocurrencias_mes(conn, hoy.year, hoy.month, hoy)
    pagos = [f for f in del_mes if f["fecha"] == hoy or (f["vencido"] and f["fecha"] >= inicio)]
    pendientes = [f for f in pagos if f["real"] is None]
    semana = proximos_movimientos(egresos, ingresos, hoy + timedelta(days=1), hoy + timedelta(days=7))

    indicadores = [
        Indicador("Capital del rulo", formato_pesos(capital_hoy, 0),
                  f"+{formato_pesos(summary.rendimiento_generado, 0)} hoy" if summary else "",
                  url=url_for("main.dashboard")),
        Indicador(
            "Pagos de hoy",
            f"{len(pendientes)} pendiente{'s' if len(pendientes) != 1 else ''}" if pendientes else "Al día",
            f"{len(pagos) - len(pendientes)} ya marcado{'s' if len(pagos) - len(pendientes) != 1 else ''}" if pagos else "Nada para hoy",
            clase="negativo" if any(f["vencido"] for f in pendientes) else "",
            url=url_for("main.este_mes"),
        ),
    ]

    return AporteHoy(
        indicadores=indicadores,
        paneles_dia=[
            panel("Pagos", "hoy/_finanzas_pagos.html", filas=pagos, hoy=hoy,
                   mes_param=f"{hoy.year}-{hoy.month}", volver=url_for("hoy.inicio")),
            panel("Próximos días", "hoy/_finanzas_proximos.html", proximos=semana),
        ],
        paneles_estado=[
            panel("El rulo hoy", "parciales/_rulo_hoy.html", actual=actual, proxima=proxima, hoy=hoy,
                   summary=summary, vencimiento=vencimiento, dias_para_vencimiento=(vencimiento - hoy).days),
            panel("Capturas de hoy", "parciales/_capturas_hoy.html", summary=summary, ahora=ahora),
        ],
        alertas=_alertas(conn, capital_por_dia, hoy, egresos, ingresos),
        chips=[f"💸 Rulo: pasar a {proxima['wallet_name']} a las {proxima['timestamp'].strftime('%H:%M')}"] if proxima else [],
    )


def aporte_rutina(conn, ahora: datetime) -> AporteHoy:
    from .rutina.rutas import aporte_hoy

    return aporte_hoy(conn, ahora)


def aporte_entrenamiento(conn, ahora: datetime) -> AporteHoy:
    from .entrenamiento.rutas import aporte_hoy

    return aporte_hoy(conn, ahora)


# Orden en que las secciones aparecen en la pantalla.
APORTES = [aporte_rutina, aporte_entrenamiento, aporte_finanzas]


@bp.route("/")
def inicio():
    conn = db.get_db()
    ahora = datetime.now()
    hoy: date = ahora.date()
    total = AporteHoy()
    for aporte in APORTES:
        parte = aporte(conn, ahora)
        total.indicadores += parte.indicadores
        total.paneles_dia += parte.paneles_dia
        total.paneles_estado += parte.paneles_estado
        total.alertas += parte.alertas
        total.chips += parte.chips

    return render_template(
        "hoy.html",
        hoy=hoy,
        fecha_larga=f"{DIAS[hoy.weekday()]} {hoy.day} de {MESES[hoy.month - 1].lower()} de {hoy.year}",
        saludo="Buen día" if ahora.hour < 13 else ("Buenas tardes" if ahora.hour < 20 else "Buenas noches"),
        indicadores=total.indicadores,
        paneles_dia=total.paneles_dia,
        paneles_estado=total.paneles_estado,
        alertas=total.alertas,
        chips=total.chips,
    )
