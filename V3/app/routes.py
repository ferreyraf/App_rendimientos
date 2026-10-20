import calendar
import csv
import io
import json
import logging
import math
import platform
import tempfile
import sys
import time
import urllib.request
from dataclasses import replace
from pathlib import Path
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, send_file, url_for

from . import cotizacion, db, formato_ar, formato_pesos, preferencias, respaldo
from .novedades import NOVEDADES
from .version import APP_ID, APP_NAME, DESCRIPTION, PREFIJO_TAG, PUBLISHER, REPO_GITHUB, __version__
from .domain import (
    MovimientoRecurrente,
    Wallet,
    aguinaldos,
    aguinaldos_por_clave,
    ajustes_por_controles,
    alertas_financieras,
    fechas_del_movimiento,
    monto_estimado,
    pago_registrado,
    billetera_actual,
    calendario_ajustes,
    capital_simple,
    capital_sin_rendimiento,
    daily_yield,
    fecha_recuperacion,
    movimientos_del_mes,
    proxima_captura,
    proximos_movimientos,
    proximo_vencimiento_impuestos,
    simulate,
    sumar_meses,
    tea_del_rulo,
    tasa_efectiva_anual,
)

bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)

# IPC mensual del INDEC, vía la API pública de argentinadatos.com.ar. Se usa
# solo para sugerir el % de los ajustes periódicos (ej. alquiler por IPC).
IPC_URL = "https://api.argentinadatos.com/v1/finanzas/indices/inflacion"
IPC_CACHE_SEGUNDOS = 6 * 3600
_ipc_cache: dict = {"timestamp": 0.0, "meses": None}


def _wallet_from_row(row):
    return Wallet(
        id=row["id"],
        name=row["name"],
        capture_time=row["capture_time"],
        payout_time=row["payout_time"],
        active_weekdays=tuple(int(x) for x in row["active_weekdays"].split(",")),
        default_tna=row["tna"],
        bundles_weekend_payout=bool(row["bundles_weekend_payout"]),
        activo=bool(row["activo"]),
        monto_minimo=row["monto_minimo"],
        monto_maximo=row["monto_maximo"],
        reparto_socio_id=row["reparto_socio_id"],
        reparto_umbral=row["reparto_umbral"],
        reparto_hora=row["reparto_hora"],
    )


def _aportes_pares(conn):
    return [(fecha, monto) for _, fecha, monto in db.get_aportes(conn)]


def _egresos(conn) -> list[MovimientoRecurrente]:
    return [m for _, m in db.get_egresos(conn)]


def _ingresos(conn) -> list[MovimientoRecurrente]:
    """Ingresos cargados más el aguinaldo automático de los que lo tienen
    activo (ver MovimientoRecurrente.aguinaldo)."""
    ingresos = [m for _, m in db.get_ingresos(conn)]
    hoy = date.today()
    return ingresos + aguinaldos(ingresos, date(hoy.year - 2, 1, 1), date(hoy.year + 3, 12, 31))


def _controles(conn, aportes, egresos, ingresos, wallets) -> list[dict]:
    """Cada control de saldo real con la diferencia contra lo simulado."""
    controles = [(fecha, monto) for _, fecha, monto in db.get_controles_saldo(conn)]
    return ajustes_por_controles(aportes, egresos, ingresos, wallets, controles) if controles else []


def _alertas(conn, capital_por_dia, hoy, egresos, ingresos) -> list[dict]:
    """Alertas de la proyección más los pagos del mes con fecha pasada que
    todavía no se marcaron en "Este mes"."""
    alertas = alertas_financieras(capital_por_dia, hoy, egresos, ingresos)
    # Solo cuenta lo que cae desde el arranque del rulo (lo anterior no entra en la simulación).
    aportes = _aportes_pares(conn)
    inicio = min((f for f, _ in aportes), default=hoy)
    vencidos = [
        f for f in _ocurrencias_mes(conn, hoy.year, hoy.month, hoy) if f["vencido"] and f["fecha"] >= inicio
    ]
    if vencidos:
        alertas.append({"nivel": "aviso", "tipo": "vencidos", "cantidad": len(vencidos)})
    return alertas


def _controles_con_id(conn) -> list[dict]:
    """Controles de saldo para listar en Configuración (con su id para borrar)."""
    aportes = _aportes_pares(conn)
    if not aportes:
        return []
    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    calculados = {c["fecha"]: c for c in _controles(conn, aportes, _egresos(conn), _ingresos(conn), wallets)}
    return [
        dict(calculados[fecha], id=id_) if fecha in calculados
        else {"id": id_, "fecha": fecha, "real": monto, "simulado": None, "diferencia": None}
        for id_, fecha, monto in db.get_controles_saldo(conn)
    ]


def _ajustes_saldo(conn, aportes, egresos, ingresos, wallets) -> list[tuple[datetime, float]]:
    """Correcciones que llevan la simulación a los saldos reales cargados."""
    return [(c["momento"], c["diferencia"]) for c in _controles(conn, aportes, egresos, ingresos, wallets)]


def _con_ajustes(aportes, ajustes) -> list[tuple[date, float]]:
    """Aportes más las correcciones de saldo, por día (para los cálculos sin
    rulo / quieto, que no trabajan con horas)."""
    return list(aportes) + [(momento.date(), monto) for momento, monto in ajustes]


# Categorías base de los egresos: fijos (alquiler, cuotas), servicios (luz,
# gas, internet), móviles (comida, transporte del día a día) y ocio. Se pueden
# sumar otras que ya existan en la base, pero estas siempre se ofrecen primero.
CATEGORIAS_GASTO = ("Gastos fijos", "Servicios", "Gastos móviles", "Ocio")

DIAS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")

MESES = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)


def _agrupar_por_mes(movimientos, hoy: date) -> dict:
    """Separa los movimientos (id, MovimientoRecurrente) en recurrentes y
    puntuales agrupados por mes. Cada mes trae el
    subtotal de sus puntuales y el total del mes sumando los recurrentes que
    caen en él — para que en Configuración se vea qué corresponde a cada mes.
    Orden: el mes actual y los próximos (cronológico), después los pasados."""
    recurrentes = [(id_, m) for id_, m in movimientos if m.recurrente]
    todos = [m for _, m in movimientos]

    por_mes: dict[tuple[int, int], list] = {}
    for id_, m in movimientos:
        if not m.recurrente and m.fecha is not None:
            por_mes.setdefault((m.fecha.year, m.fecha.month), []).append((id_, m))

    actual = (hoy.year, hoy.month)
    proximos = sorted(k for k in por_mes if k >= actual)
    pasados = sorted((k for k in por_mes if k < actual), reverse=True)
    meses = []
    for (anio, mes) in proximos + pasados:
        items = sorted(por_mes[(anio, mes)], key=lambda par: par[1].fecha)
        meses.append({
            "nombre": f"{MESES[mes - 1]} {anio}",
            "movimientos": items,
            "subtotal": sum(m.monto_en_pesos for _, m in items),
            "total_mes": sum(m.monto for m in movimientos_del_mes(todos, anio, mes)),
            "actual": (anio, mes) == (hoy.year, hoy.month),
            "abierto": (anio, mes) >= (hoy.year, hoy.month),
        })

    return {
        "recurrentes": recurrentes,
        "total_recurrentes": sum(m.monto_en_pesos for _, m in recurrentes),
        "meses": meses,
        "mes_actual_nombre": f"{MESES[hoy.month - 1]} {hoy.year}",
        "total_mes_actual": sum(m.monto for m in movimientos_del_mes(todos, hoy.year, hoy.month)),
    }


def _flujo_mensual(aportes, egresos, ingresos, hoy: date) -> list[dict]:
    """Ingresos y egresos resueltos mes a mes (recurrentes + puntuales), desde
    el primer aporte (o el primer movimiento puntual, si es anterior) hasta el
    último movimiento puntual cargado o el mes actual. Los meses posteriores al
    actual se marcan como proyectados."""
    puntuales = [m.fecha for m in egresos + ingresos if not m.recurrente and m.fecha]
    inicio = min([fecha for fecha, _ in aportes] + puntuales + [hoy])
    fin = max(puntuales + [hoy])

    meses = []
    anio, mes = inicio.year, inicio.month
    while (anio, mes) <= (fin.year, fin.month):
        egresos_mes = movimientos_del_mes(egresos, anio, mes)
        ingresos_mes = movimientos_del_mes(ingresos, anio, mes)

        por_categoria: dict[str, float] = {}
        for m in egresos_mes:
            clave = m.categoria or "Sin categoría"
            por_categoria[clave] = por_categoria.get(clave, 0.0) + m.monto

        # Listas (no dicts) para que `tojson` no reordene alfabéticamente.
        meses.append({
            "clave": f"{anio}-{mes:02d}",
            "nombre": f"{MESES[mes - 1]} {anio}",
            "corto": f"{MESES[mes - 1][:3]} {str(anio)[2:]}",
            "proyectado": (anio, mes) > (hoy.year, hoy.month),
            "ingresos": sum(m.monto for m in ingresos_mes),
            "egresos": sum(m.monto for m in egresos_mes),
            "categorias": sorted(
                ({"nombre": k, "monto": v} for k, v in por_categoria.items()),
                key=lambda c: c["monto"], reverse=True,
            ),
            "detalle_ingresos": [{"etiqueta": m.etiqueta, "monto": m.monto} for m in ingresos_mes],
            "detalle_egresos": [
                {"etiqueta": m.etiqueta, "monto": m.monto, "categoria": m.categoria or "Sin categoría"}
                for m in egresos_mes
            ],
        })
        anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return meses


def _datos_movimiento(tipo: str, id_: int, m) -> dict:
    """Todo lo que el panel de edición de Configuración necesita de un
    egreso/ingreso (se serializa a JSON en la fila)."""
    return {
        "id": id_,
        "etiqueta": m.etiqueta,
        "categoria": m.categoria,
        "monto": m.monto,
        "moneda": m.moneda,
        "monto_pesos": m.monto_en_pesos,
        "recurrente": m.recurrente,
        "fecha": m.fecha.isoformat() if m.fecha and not m.recurrente else "",
        "desde": m.fecha.isoformat() if m.fecha and m.recurrente else "",
        "dia_mes": m.dia_mes or "",
        "dia_habil": m.dia_habil,
        "aumento_pct": m.aumento_pct or "",
        "aumento_periodo_meses": m.aumento_periodo_meses or 4,
        "aumento_inicio": m.aumento_inicio.isoformat() if m.aumento_inicio else "",
        "hasta": m.hasta.isoformat() if m.hasta else "",
        "aguinaldo": str(m.aguinaldo),
        "aguinaldo_negro_pct": m.aguinaldo_negro_pct or "",
        "ajustes": [
            {"mes": f"{MESES[f.month - 1][:3]} {f.year}", "pct": pct} for f, pct in m.ajustes
        ],
        "url_editar": url_for(f"main.editar_{tipo}", **{f"{tipo}_id": id_}),
        "url_eliminar": url_for(f"main.eliminar_{tipo}", **{f"{tipo}_id": id_}),
    }


def _ajustes_configurables(egresos, ingresos, hoy: date) -> list[dict]:
    """Movimientos recurrentes con aumento periódico configurado, con su
    calendario de ajustes hasta 12 meses adelante — para cargar el % real de
    cada ajuste en Configuración."""
    hasta = sumar_meses(hoy, 12)
    resultado = []
    for tipo, items in (("egreso", egresos), ("ingreso", ingresos)):
        for id_, m in items:
            if not m.recurrente or m.aumento_inicio is None or not (m.aumento_pct or m.ajustes):
                continue
            calendario = calendario_ajustes(m, hasta)
            for ajuste in calendario:
                ajuste["nombre_mes"] = f"{MESES[ajuste['fecha'].month - 1]} {ajuste['fecha'].year}"
                ajuste["futuro"] = ajuste["fecha"] > hoy
            resultado.append({
                "tipo": tipo,
                "id": id_,
                "mov": m,
                "calendario": calendario,
                "monto_hoy": next(
                    (a["monto"] for a in reversed(calendario) if a["fecha"] <= hoy), m.monto_en_pesos
                ),
            })
    return resultado


def _total_invertido(conn) -> float:
    return sum(monto for _, _, monto, _ in db.get_inversiones(conn))


def _parse_inversion_form(form) -> tuple[str, float, float]:
    etiqueta = (form.get("etiqueta") or "").strip()
    if not etiqueta:
        raise ValueError("La etiqueta es obligatoria.")
    monto = _numero(form["monto"])
    if monto <= 0:
        raise ValueError("El monto debe ser mayor a cero.")
    tna = _numero(form.get("tna") or 0, "la TNA")
    if tna < 0:
        raise ValueError("La TNA no puede ser negativa.")
    return etiqueta, monto, tna


MONEDAS = ("ARS", "USD")

# Tope para cualquier número cargado: muy por encima de cualquier monto real,
# pero lejos de los valores que desbordan la simulación a infinito.
NUMERO_MAXIMO = 1e12

# Hasta dónde se puede simular hacia adelante (proyección, calculadora,
# recuperación): más allá la simulación día a día tarda minutos.
ANIOS_MAXIMOS_SIMULACION = 30


def _numero(valor, campo: str = "el monto") -> float:
    """float() que además rechaza NaN, infinitos y valores desmedidos: SQLite
    guarda NaN como NULL y los infinitos rompen los cálculos después."""
    try:
        numero = float(str(valor).strip())
    except (TypeError, ValueError):
        raise ValueError(f"Revisá {campo}: no es un número válido.") from None
    if not math.isfinite(numero) or abs(numero) > NUMERO_MAXIMO:
        raise ValueError(f"Revisá {campo}: no es un número válido.")
    return numero


def _entero(valor, campo: str) -> int:
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        raise ValueError(f"Revisá {campo}: tiene que ser un número entero.") from None


def _fecha(valor, campo: str) -> date:
    try:
        return date.fromisoformat(str(valor).strip())
    except (TypeError, ValueError):
        raise ValueError(f"Revisá {campo}: la fecha no es válida.") from None


def _hora(valor, campo: str) -> str:
    """Hora HH:MM válida (una hora mal guardada en una billetera rompe la
    simulación en todas las pantallas)."""
    try:
        return datetime.strptime(str(valor).strip(), "%H:%M").strftime("%H:%M")
    except (TypeError, ValueError):
        raise ValueError(f"Revisá {campo}: tiene que ser una hora válida (HH:MM).") from None


def ruta_interna(valor: str | None, defecto: str) -> str:
    """`valor` si es una ruta de esta app; si no, `defecto`. "//sitio" o
    "/\\sitio" el navegador los toma como otro dominio."""
    if not valor or not valor.startswith("/") or valor.startswith(("//", "/\\")):
        return defecto
    return valor


def _fecha_limite_simulacion(hoy: date) -> date:
    return sumar_meses(hoy, 12 * ANIOS_MAXIMOS_SIMULACION)


@bp.before_app_request
def _refrescar_cotizacion():
    if request.endpoint and request.endpoint != "static":
        cotizacion.refrescar_si_vieja(db.get_db())


# Acciones que no deben volver a publicar la base al terminar (ya la manejan).
_SIN_PUBLICAR = {"main.sincronizar_ahora", "main.guardar_sync", "main.restaurar_respaldo"}


@bp.before_app_request
def _traer_cambios_de_otra_pc():
    """Si la base compartida cambió (otra PC guardó algo), traerla."""
    if request.method == "GET" and request.endpoint not in (None, "static") and not request.path.startswith("/api/"):
        mensaje = respaldo.traer_si_cambio()
        if mensaje and mensaje != "publicada":
            flash(mensaje, "success")


@bp.after_app_request
def _publicar_cambios(respuesta):
    """Después de cada cambio guardado, publicar la base en la carpeta compartida."""
    if (
        request.method == "POST"
        and respuesta.status_code in (200, 302)
        and request.endpoint not in _SIN_PUBLICAR
        and respaldo.carpeta_sync() is not None
    ):
        respaldo.publicar()
    return respuesta


def _parse_moneda(form, conn) -> str:
    """Moneda del formulario. Para cargar algo en dólares tiene que haber una
    cotización guardada (si no hay, se intenta consultar en el momento)."""
    moneda = form.get("moneda") or "ARS"
    if moneda not in MONEDAS:
        raise ValueError("Moneda inválida.")
    if moneda == "USD" and db.get_cotizacion(conn, "USD") is None and cotizacion.actualizar(conn) is None:
        raise ValueError("No se pudo obtener la cotización del dólar (infodolar). Probá de nuevo con conexión.")
    return moneda


def _parse_extras(form) -> tuple[date | None, int, float]:
    """`hasta` (fin de un recurrente, ej. la última cuota), el modo de
    aguinaldo automático (0 = no, 1 = 30/06 y 18/12, 2 = con el sueldo de
    julio y enero) y el % de ese aguinaldo que se cobra en negro. Solo
    aplican a recurrentes."""
    if form.get("recurrente") != "on":
        return None, 0, 0.0
    hasta_raw = (form.get("hasta") or "").strip()
    hasta = _fecha(hasta_raw, "la fecha \"hasta\"") if hasta_raw else None
    desde_raw = (form.get("desde") or "").strip()
    if hasta and desde_raw and hasta < _fecha(desde_raw, "la fecha \"desde\""):
        raise ValueError("La fecha \"hasta\" no puede ser anterior a \"desde\".")
    aguinaldo = _entero(form.get("aguinaldo") or 0, "la opción de aguinaldo")
    if aguinaldo not in (0, 1, 2):
        raise ValueError("Opción de aguinaldo inválida.")
    negro_raw = (form.get("aguinaldo_negro_pct") or "").strip()
    negro = _numero(negro_raw, "el % de aguinaldo en negro") if negro_raw and aguinaldo else 0.0
    if not 0 <= negro <= 100:
        raise ValueError("El % de aguinaldo en negro tiene que estar entre 0 y 100.")
    return hasta, aguinaldo, negro


def _parse_movimiento_form(
    form,
) -> tuple[str, float, bool, date | None, int | None, bool, str, float, date | None, int]:
    etiqueta = (form.get("etiqueta") or "").strip()
    if not etiqueta:
        raise ValueError("La etiqueta es obligatoria.")
    monto = _numero(form["monto"])
    if monto <= 0:
        raise ValueError("El monto debe ser mayor a cero.")
    categoria = (form.get("categoria") or "").strip()
    recurrente = form.get("recurrente") == "on"
    aumento_pct = 0.0
    aumento_inicio = None
    aumento_periodo_meses = 4
    if recurrente:
        dia_mes = _entero(form["dia_mes"], "el día del mes")
        dia_habil = form.get("dia_habil") == "on"
        limite = 23 if dia_habil else 31
        if not (1 <= dia_mes <= limite):
            raise ValueError(f"El día {'hábil' if dia_habil else 'del mes'} debe estar entre 1 y {limite}.")
        # En un recurrente, `fecha` es el "desde" opcional (ver MovimientoRecurrente).
        desde_raw = (form.get("desde") or "").strip()
        fecha = _fecha(desde_raw, "la fecha \"desde\"") if desde_raw else None
        aumento_pct = _numero(form.get("aumento_pct") or 0, "el aumento periódico")
        if aumento_pct < 0:
            raise ValueError("El aumento periódico no puede ser negativo.")
        aumento_periodo_meses = _entero(form.get("aumento_periodo_meses") or 4, "el período del aumento")
        if aumento_periodo_meses < 1:
            raise ValueError("El período del aumento debe ser de al menos 1 mes.")
        aumento_inicio_raw = (form.get("aumento_inicio") or "").strip()
        if aumento_pct > 0:
            if not aumento_inicio_raw:
                raise ValueError("Indicá desde cuándo se cuenta el aumento periódico.")
            aumento_inicio = _fecha(aumento_inicio_raw, "el inicio del aumento")
        elif aumento_inicio_raw:
            aumento_inicio = _fecha(aumento_inicio_raw, "el inicio del aumento")
    else:
        fecha = _fecha(form["fecha"], "la fecha")
        dia_mes = None
        dia_habil = False
    return (
        etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria,
        aumento_pct, aumento_inicio, aumento_periodo_meses,
    )


def estado_rulo(ahora: datetime, wallets) -> tuple[dict | None, dict | None]:
    """Billetera donde está la plata ahora y la próxima captura (con cuánto
    falta), para el Resumen y la pantalla Hoy."""
    actual_info = None
    actual = billetera_actual(ahora, wallets)
    if actual is not None:
        ts, wallet = actual
        actual_info = {"wallet_name": wallet.name, "timestamp": ts}

    proxima_info = None
    proxima = proxima_captura(ahora, wallets)
    if proxima is not None:
        ts, wallet = proxima
        horas, resto = divmod(int((ts - ahora).total_seconds()), 3600)
        proxima_info = {"wallet_name": wallet.name, "timestamp": ts, "horas": horas, "minutos": resto // 60}
    return actual_info, proxima_info


@bp.route("/finanzas")
def dashboard():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        return redirect(url_for("main.configuracion", _anchor="aportes"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    ajustes = _ajustes_saldo(conn, aportes, egresos, ingresos, wallets)
    total_invertido = _total_invertido(conn)

    ahora = datetime.now()
    hoy = ahora.date()
    summaries = simulate(aportes, egresos, ingresos, hoy, wallets, ajustes)
    summary = summaries[-1] if summaries else None

    actual_info, proxima_info = estado_rulo(ahora, wallets)

    principal_total = sum(monto for _, monto in aportes)
    capital_hoy = summary.capital_cierre if summary else principal_total

    mejor_quieto = max(
        (capital_simple(_con_ajustes(aportes, ajustes), w.default_tna, hoy, egresos, ingresos) for w in wallets if w.activo),
        default=principal_total,
    )
    ventaja_rulo = capital_hoy - mejor_quieto

    total_aportado = principal_total
    patrimonio_total = capital_hoy + total_invertido

    vencimiento = proximo_vencimiento_impuestos(hoy)
    dias_para_vencimiento = (vencimiento - hoy).days

    egresos_mes = movimientos_del_mes(egresos, hoy.year, hoy.month)
    ingresos_mes = movimientos_del_mes(ingresos, hoy.year, hoy.month)
    total_egresos_mes = sum(m.monto for m in egresos_mes)
    total_ingresos_mes = sum(m.monto for m in ingresos_mes)

    # Gastos del mes por categoría: siempre las categorías base (aunque estén
    # en cero, para ver qué falta cargar) más cualquier otra que exista.
    por_categoria = {cat: 0.0 for cat in CATEGORIAS_GASTO}
    for m in egresos_mes:
        clave = m.categoria or "Sin categoría"
        por_categoria[clave] = por_categoria.get(clave, 0.0) + m.monto
    egresos_por_categoria = [
        {
            "categoria": k,
            "monto": v,
            "pct": (v / total_egresos_mes * 100) if total_egresos_mes else 0.0,
        }
        for k, v in sorted(por_categoria.items(), key=lambda kv: kv[1], reverse=True)
    ]

    # Capital al cierre del mes y a ~9 meses, con rulo y sin él (la misma plata
    # sin ningún interés): la diferencia es lo que aporta el rulo.
    fin_mes = date(hoy.year, hoy.month, calendar.monthrange(hoy.year, hoy.month)[1])
    horizonte = sumar_meses(hoy, 9) - timedelta(days=1)
    proyectado = {s.date: s.capital_cierre for s in simulate(aportes, egresos, ingresos, horizonte, wallets, ajustes)}
    sin_rulo = capital_sin_rendimiento(_con_ajustes(aportes, ajustes), egresos, ingresos, horizonte)
    rendimiento_acumulado = capital_hoy - sin_rulo.get(hoy, capital_hoy)
    capital_fin_mes = proyectado.get(fin_mes, capital_hoy)
    rendimiento_resto_mes = (capital_fin_mes - sin_rulo.get(fin_mes, capital_fin_mes)) - rendimiento_acumulado
    con_rulo_horizonte = proyectado.get(horizonte, capital_hoy)
    sin_rulo_horizonte = sin_rulo.get(horizonte, capital_hoy)

    proximo_ajuste = None
    for m in egresos + ingresos:
        if not m.recurrente or m.aumento_inicio is None or not (m.aumento_pct or m.ajustes):
            continue
        siguiente = next((a for a in calendario_ajustes(m, horizonte) if a["fecha"] > hoy), None)
        if siguiente and (proximo_ajuste is None or siguiente["fecha"] < proximo_ajuste["fecha"]):
            proximo_ajuste = dict(siguiente, etiqueta=m.etiqueta)

    proximos = proximos_movimientos(egresos, ingresos, hoy + timedelta(days=1), hoy + timedelta(days=45))[:6]

    return render_template(
        "dashboard.html",
        summary=summary,
        hoy=hoy,
        dia_semana=DIAS[hoy.weekday()],
        ahora=ahora,
        proxima=proxima_info,
        actual=actual_info,
        ventaja_rulo=ventaja_rulo,
        total_aportado=total_aportado,
        total_invertido=total_invertido,
        patrimonio_total=patrimonio_total,
        vencimiento=vencimiento,
        dias_para_vencimiento=dias_para_vencimiento,
        nombre_mes=MESES[hoy.month - 1].lower(),
        ingresos_mes=ingresos_mes,
        egresos_mes=egresos_mes,
        total_egresos_mes=total_egresos_mes,
        total_ingresos_mes=total_ingresos_mes,
        egresos_por_categoria=egresos_por_categoria,
        capital_hoy=capital_hoy,
        fin_mes=fin_mes,
        capital_fin_mes=capital_fin_mes,
        rendimiento_resto_mes=rendimiento_resto_mes,
        rendimiento_acumulado=rendimiento_acumulado,
        horizonte=horizonte,
        nombre_horizonte=f"{MESES[horizonte.month - 1].lower()} {horizonte.year}",
        con_rulo_horizonte=con_rulo_horizonte,
        sin_rulo_horizonte=sin_rulo_horizonte,
        proximo_ajuste=proximo_ajuste,
        nombre_mes_ajuste=(
            f"{MESES[proximo_ajuste['fecha'].month - 1][:3].lower()} {str(proximo_ajuste['fecha'].year)[2:]}"
            if proximo_ajuste else ""
        ),
        proximos=proximos,
        cotizacion_usd=db.get_cotizacion(conn, "USD"),
        ultimo_control=(_controles(conn, aportes, egresos, ingresos, wallets) or [None])[-1],
        alertas=_alertas(conn, proyectado, hoy, egresos, ingresos),
    )


@bp.route("/configuracion", methods=["GET", "POST"])
def configuracion():
    conn = db.get_db()

    if request.method == "POST":
        try:
            monto = _numero(request.form["monto"])
            fecha = date.fromisoformat(request.form["fecha"])
        except (KeyError, ValueError):
            flash("Datos inválidos: revisá el monto y la fecha.", "error")
            return redirect(url_for("main.configuracion", _anchor="aportes"))

        if monto <= 0:
            flash("El aporte debe ser mayor a cero.", "error")
            return redirect(url_for("main.configuracion", _anchor="aportes"))
        if fecha > date.today():
            flash("La fecha del aporte no puede ser futura.", "error")
            return redirect(url_for("main.configuracion", _anchor="aportes"))

        db.add_aporte(conn, fecha, monto)
        flash("Aporte agregado.", "success")
        return redirect(url_for("main.configuracion", _anchor="aportes"))

    aportes = db.get_aportes(conn)
    wallets = db.get_wallets(conn)
    egresos = db.get_egresos(conn)
    ingresos = db.get_ingresos(conn)
    hoy = date.today()
    otras = sorted({m.categoria for _, m in egresos if m.categoria} - set(CATEGORIAS_GASTO))
    categorias_existentes = list(CATEGORIAS_GASTO) + otras
    return render_template(
        "configuracion.html",
        aportes=aportes,
        wallets=wallets,
        egresos=_agrupar_por_mes(egresos, hoy),
        ingresos=_agrupar_por_mes(ingresos, hoy),
        ajustes=_ajustes_configurables(egresos, ingresos, hoy),
        datos={
            "egreso": {id_: _datos_movimiento("egreso", id_, m) for id_, m in egresos},
            "ingreso": {id_: _datos_movimiento("ingreso", id_, m) for id_, m in ingresos},
        },
        conteo_categorias={
            cat: sum(1 for _, m in egresos if m.categoria == cat) for cat in categorias_existentes
        },
        cotizacion_usd=db.get_cotizacion(conn, "USD"),
        controles=_controles_con_id(conn),
        sync=respaldo.estado_sync(),
        prefs=preferencias.cargar(),
        carpeta_backups=db.BACKUP_DIR,
        cantidad_backups=len(list(db.BACKUP_DIR.glob("*.db"))) if db.BACKUP_DIR.exists() else 0,
        total_aportes=sum(monto for _, _, monto in aportes),
        categorias_existentes=categorias_existentes,
    )


@bp.route("/configuracion/editar/<int:aporte_id>", methods=["POST"])
def editar_aporte(aporte_id):
    conn = db.get_db()
    try:
        monto = _numero(request.form["monto"])
        fecha = date.fromisoformat(request.form["fecha"])
    except (KeyError, ValueError):
        flash("Datos inválidos: revisá el monto y la fecha.", "error")
        return redirect(url_for("main.configuracion", _anchor="aportes"))

    if monto <= 0:
        flash("El aporte debe ser mayor a cero.", "error")
        return redirect(url_for("main.configuracion", _anchor="aportes"))
    if fecha > date.today():
        flash("La fecha del aporte no puede ser futura.", "error")
        return redirect(url_for("main.configuracion", _anchor="aportes"))

    if db.update_aporte(conn, aporte_id, fecha, monto):
        flash("Aporte actualizado.", "success")
    else:
        flash("Ese aporte ya no existe.", "error")
    return redirect(url_for("main.configuracion", _anchor="aportes"))


@bp.route("/configuracion/eliminar/<int:aporte_id>", methods=["POST"])
def eliminar_aporte(aporte_id):
    conn = db.get_db()
    db.delete_aporte(conn, aporte_id)
    flash("Aporte eliminado.", "success")
    return redirect(url_for("main.configuracion", _anchor="aportes"))


@bp.route("/configuracion/egreso", methods=["POST"])
def agregar_egreso():
    conn = db.get_db()
    try:
        (
            etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria,
            aumento_pct, aumento_inicio, aumento_periodo_meses,
        ) = _parse_movimiento_form(request.form)
        moneda = _parse_moneda(request.form, conn)
        hasta, aguinaldo, aguinaldo_negro_pct = _parse_extras(request.form)
    except KeyError:
        flash("Datos inválidos: revisá los campos del egreso.", "error")
        return redirect(url_for("main.configuracion", _anchor="gastos"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.configuracion", _anchor="gastos"))

    nuevo_id = db.add_egreso(
        conn, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria,
        aumento_pct, aumento_inicio, aumento_periodo_meses, moneda,
    )
    db.guardar_extras(conn, "egresos", nuevo_id, hasta, 0)
    flash("Egreso agregado.", "success")
    return redirect(url_for("main.configuracion", _anchor="gastos"))


@bp.route("/configuracion/egreso/editar/<int:egreso_id>", methods=["POST"])
def editar_egreso(egreso_id):
    conn = db.get_db()
    try:
        (
            etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria,
            aumento_pct, aumento_inicio, aumento_periodo_meses,
        ) = _parse_movimiento_form(request.form)
        moneda = _parse_moneda(request.form, conn)
        hasta, aguinaldo, aguinaldo_negro_pct = _parse_extras(request.form)
    except KeyError:
        flash("Datos inválidos: revisá los campos del egreso.", "error")
        return redirect(url_for("main.configuracion", _anchor="gastos"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.configuracion", _anchor="gastos"))

    if not db.update_egreso(
        conn, egreso_id, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria,
        aumento_pct, aumento_inicio, aumento_periodo_meses, moneda,
    ):
        flash("Ese egreso ya no existe.", "error")
        return redirect(url_for("main.configuracion", _anchor="gastos"))
    db.guardar_extras(conn, "egresos", egreso_id, hasta, 0)
    flash("Egreso actualizado.", "success")
    return redirect(url_for("main.configuracion", _anchor="gastos"))


@bp.route("/configuracion/egreso/eliminar/<int:egreso_id>", methods=["POST"])
def eliminar_egreso(egreso_id):
    conn = db.get_db()
    db.delete_egreso(conn, egreso_id)
    flash("Egreso eliminado.", "success")
    return redirect(url_for("main.configuracion", _anchor="gastos"))


_TABLAS_AJUSTE = {"egreso": ("egresos", db.get_egresos), "ingreso": ("ingresos", db.get_ingresos)}


@bp.route("/configuracion/ajuste/<tipo>/<int:mov_id>", methods=["POST"])
def guardar_ajuste(tipo, mov_id):
    """Carga (o corrige) el % real de un ajuste periódico. El mes tiene que
    ser uno de los meses de ajuste del movimiento."""
    if tipo not in _TABLAS_AJUSTE:
        return redirect(url_for("main.configuracion"))
    tabla, getter = _TABLAS_AJUSTE[tipo]
    conn = db.get_db()
    mov = dict(getter(conn)).get(mov_id)
    try:
        fecha = date.fromisoformat(request.form["fecha"])
        pct = _numero(request.form["pct"].replace(",", "."), "el porcentaje")
    except (KeyError, ValueError):
        flash("Datos inválidos: revisá el porcentaje del ajuste.", "error")
        return redirect(url_for("main.configuracion", _anchor="ajustes"))

    if mov is None or mov.aumento_inicio is None:
        flash("Ese movimiento no tiene aumento periódico configurado.", "error")
    elif fecha not in [a["fecha"] for a in calendario_ajustes(mov, fecha)]:
        flash("Ese mes no corresponde a un ajuste del movimiento.", "error")
    elif not -50 <= pct <= 500:
        flash("El porcentaje del ajuste tiene que estar entre -50% y 500%.", "error")
    else:
        db.guardar_ajuste(conn, tabla, mov_id, fecha, pct)
        flash(f"Ajuste de {MESES[fecha.month - 1]} {fecha.year} de {mov.etiqueta} guardado.", "success")
    return redirect(url_for("main.configuracion", _anchor="ajustes"))


@bp.route("/configuracion/ajuste/<tipo>/<int:mov_id>/<fecha>/eliminar", methods=["POST"])
def eliminar_ajuste(tipo, mov_id, fecha):
    """Borra el % real cargado: el período vuelve a usar el % estimado."""
    if tipo in _TABLAS_AJUSTE:
        try:
            db.eliminar_ajuste(db.get_db(), _TABLAS_AJUSTE[tipo][0], mov_id, date.fromisoformat(fecha))
            flash("El ajuste volvió al % estimado.", "success")
        except ValueError:
            flash("Fecha de ajuste inválida.", "error")
    return redirect(url_for("main.configuracion", _anchor="ajustes"))


@bp.route("/configuracion/cotizacion/actualizar", methods=["POST"])
def actualizar_cotizacion():
    nueva = cotizacion.actualizar(db.get_db())
    if nueva is None:
        flash("No se pudo consultar infodolar. Queda la última cotización guardada.", "error")
    else:
        flash(f"Dólar actualizado: ${formato_ar(nueva['valor'])}.", "success")
    return redirect(url_for("main.configuracion", _anchor="dolar"))


@bp.route("/acerca")
def acerca():
    from .actualizacion import listar_respaldos

    conn = db.get_db()
    tamano_db = db.DB_PATH.stat().st_size if db.DB_PATH.exists() else 0
    return render_template(
        "acerca.html",
        nombre=APP_NAME,
        version=__version__,
        descripcion=DESCRIPTION,
        autor=PUBLISHER,
        carpeta_datos=db.DATA_DIR,
        base=db.DB_PATH,
        tamano_db_kb=tamano_db / 1024,
        carpeta_backups=db.BACKUP_DIR,
        carpeta_logs=db.DATA_DIR / "logs",
        instalada=getattr(sys, "frozen", False),
        python=platform.python_version(),
        sistema=f"{platform.system()} {platform.release()}",
        cotizacion_usd=db.get_cotizacion(conn, "USD"),
        novedades=NOVEDADES,
        respaldos=listar_respaldos(),
    )


_version_cache: dict = {"timestamp": 0.0, "datos": None}


def _como_tupla(version: str) -> tuple[int, ...]:
    """(3, 0, 1) para "3.0.1" o el tag "bitacora-v3.0.1". Los tags de otras
    apps del mismo repositorio (los "v2.*" de la V2) no cuentan: () ."""
    if not version[:1].isdigit():
        if not version.startswith(PREFIJO_TAG):
            return ()
        version = version[len(PREFIJO_TAG):]
    try:
        return tuple(int(p) for p in version.split("."))
    except ValueError:
        return ()


@bp.route("/api/version")
def api_version():
    """Última release publicada en GitHub, si es más nueva que esta. Se
    cachea unas horas; sin conexión responde sin novedades."""
    ahora = time.time()
    if _version_cache["datos"] is None or ahora - _version_cache["timestamp"] > 6 * 3600:
        nueva = None
        try:
            pedido = urllib.request.Request(
                f"https://api.github.com/repos/{REPO_GITHUB}/releases?per_page=20",
                headers={"User-Agent": f"{APP_ID}/{__version__}", "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(pedido, timeout=8) as resp:
                releases = json.load(resp)
            candidatas = [
                r for r in releases
                if not r.get("draft") and not r.get("prerelease") and _como_tupla(r.get("tag_name", ""))
            ]
            mejor = max(candidatas, key=lambda r: _como_tupla(r["tag_name"]), default=None)
            if mejor and _como_tupla(mejor["tag_name"]) > _como_tupla(__version__):
                # El aviso del menú lleva a Acerca de, donde se instala.
                nueva = {"version": mejor["tag_name"].removeprefix(PREFIJO_TAG),
                         "url": url_for("main.acerca", _anchor="actualizar")}
        except (OSError, ValueError, KeyError, TypeError):
            logger.info("No se pudo consultar la última versión", exc_info=True)
            return jsonify({"actual": __version__, "nueva": None})
        _version_cache.update(timestamp=ahora, datos={"actual": __version__, "nueva": nueva})
    return jsonify(_version_cache["datos"])


@bp.route("/api/ipc")
def api_ipc():
    """IPC mensual (% de variación) de los últimos 36 meses, cacheado unas
    horas. Si la API no responde, devuelve 503 y la pantalla sigue andando
    sin las sugerencias."""
    ahora = time.time()
    if _ipc_cache["meses"] is None or ahora - _ipc_cache["timestamp"] > IPC_CACHE_SEGUNDOS:
        try:
            # Sin User-Agent propio la API responde 403 al de urllib.
            pedido = urllib.request.Request(IPC_URL, headers={"User-Agent": f"{APP_ID}/{__version__}"})
            with urllib.request.urlopen(pedido, timeout=8) as resp:
                datos = json.load(resp)
            _ipc_cache["meses"] = [
                {"mes": d["fecha"][:7], "valor": float(d["valor"])} for d in datos
            ][-36:]
            _ipc_cache["timestamp"] = ahora
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("No se pudo consultar el IPC", exc_info=True)
            if _ipc_cache["meses"] is None:
                return jsonify({"error": "No se pudo consultar el IPC."}), 503
    return jsonify({"meses": _ipc_cache["meses"]})


@bp.route("/configuracion/ingreso", methods=["POST"])
def agregar_ingreso():
    conn = db.get_db()
    try:
        (
            etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, _categoria,
            aumento_pct, aumento_inicio, aumento_periodo_meses,
        ) = _parse_movimiento_form(request.form)
        moneda = _parse_moneda(request.form, conn)
        hasta, aguinaldo, aguinaldo_negro_pct = _parse_extras(request.form)
    except KeyError:
        flash("Datos inválidos: revisá los campos del ingreso.", "error")
        return redirect(url_for("main.configuracion", _anchor="ingresos"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.configuracion", _anchor="ingresos"))

    nuevo_id = db.add_ingreso(
        conn, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil,
        aumento_pct, aumento_inicio, aumento_periodo_meses, moneda,
    )
    db.guardar_extras(conn, "ingresos", nuevo_id, hasta, aguinaldo, aguinaldo_negro_pct)
    flash("Ingreso agregado.", "success")
    return redirect(url_for("main.configuracion", _anchor="ingresos"))


@bp.route("/configuracion/ingreso/editar/<int:ingreso_id>", methods=["POST"])
def editar_ingreso(ingreso_id):
    conn = db.get_db()
    try:
        (
            etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, _categoria,
            aumento_pct, aumento_inicio, aumento_periodo_meses,
        ) = _parse_movimiento_form(request.form)
        moneda = _parse_moneda(request.form, conn)
        hasta, aguinaldo, aguinaldo_negro_pct = _parse_extras(request.form)
    except KeyError:
        flash("Datos inválidos: revisá los campos del ingreso.", "error")
        return redirect(url_for("main.configuracion", _anchor="ingresos"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.configuracion", _anchor="ingresos"))

    if not db.update_ingreso(
        conn, ingreso_id, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil,
        aumento_pct, aumento_inicio, aumento_periodo_meses, moneda,
    ):
        flash("Ese ingreso ya no existe.", "error")
        return redirect(url_for("main.configuracion", _anchor="ingresos"))
    db.guardar_extras(conn, "ingresos", ingreso_id, hasta, aguinaldo, aguinaldo_negro_pct)
    flash("Ingreso actualizado.", "success")
    return redirect(url_for("main.configuracion", _anchor="ingresos"))


@bp.route("/configuracion/ingreso/eliminar/<int:ingreso_id>", methods=["POST"])
def eliminar_ingreso(ingreso_id):
    conn = db.get_db()
    db.delete_ingreso(conn, ingreso_id)
    flash("Ingreso eliminado.", "success")
    return redirect(url_for("main.configuracion", _anchor="ingresos"))


def _ocurrencias_mes(conn, anio: int, mes: int, hoy: date) -> list[dict]:
    """Cada ingreso/egreso que cae en el mes (aguinaldo incluido), con su
    monto estimado y, si ya se cargó, el real."""
    primero = date(anio, mes, 1)
    ultimo = date(anio, mes, calendar.monthrange(anio, mes)[1])
    filas = []
    for tipo, items in (("ingreso", db.get_ingresos(conn)), ("egreso", db.get_egresos(conn))):
        for id_, m in items:
            derivados = [("", m)]
            if tipo == "ingreso":
                derivados += aguinaldos_por_clave([m], primero, ultimo)
            for clave, item in derivados:
                for fecha in fechas_del_movimiento(item, primero, ultimo):
                    real = pago_registrado(item, fecha)
                    filas.append({
                        "tipo": tipo,
                        "id": id_,
                        "clave": clave,
                        "fecha": fecha,
                        "etiqueta": item.etiqueta,
                        "categoria": item.categoria,
                        "moneda": item.moneda if not clave else "ARS",
                        "monto_original": item.monto,
                        "estimado": monto_estimado(item, fecha),
                        "real": real,
                        "vencido": real is None and fecha < hoy,
                    })
    filas.sort(key=lambda f: (f["fecha"], f["tipo"] != "ingreso", f["etiqueta"]))
    return filas


@bp.route("/saldo-real", methods=["POST"])
def cargar_saldo_real():
    """Saldo real total del rulo a una fecha: la simulación se recalibra a
    ese valor al cierre de ese día."""
    volver = ruta_interna(request.form.get("volver"), url_for("main.dashboard"))
    try:
        monto = _numero(request.form["monto"], "el saldo")
        fecha = date.fromisoformat(request.form.get("fecha") or date.today().isoformat())
    except (KeyError, ValueError):
        flash("Datos inválidos: revisá el saldo.", "error")
        return redirect(volver)
    if monto < 0:
        flash("El saldo real no puede ser negativo.", "error")
        return redirect(volver)
    if fecha > date.today():
        flash("El saldo real no puede ser de una fecha futura.", "error")
        return redirect(volver)
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes or fecha < min(f for f, _ in aportes):
        flash("La fecha tiene que ser posterior al primer aporte.", "error")
        return redirect(volver)
    db.add_control_saldo(conn, fecha, monto)
    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    control = next(
        (c for c in _controles(conn, aportes, _egresos(conn), _ingresos(conn), wallets) if c["fecha"] == fecha), None
    )
    if control is not None:
        dif = control["diferencia"]
        detalle = "coincidía con la simulación" if abs(dif) < 1 else (
            f"la simulación estaba {formato_pesos(abs(dif))} {'abajo' if dif > 0 else 'arriba'}"
        )
        flash(f"Saldo real guardado: {formato_pesos(monto)} ({detalle}).", "success")
    return redirect(volver)


@bp.route("/saldo-real/<int:control_id>/eliminar", methods=["POST"])
def eliminar_saldo_real(control_id):
    db.delete_control_saldo(db.get_db(), control_id)
    flash("Control de saldo eliminado.", "success")
    return redirect(url_for("main.configuracion", _anchor="aportes"))


@bp.route("/preferencias/avisos", methods=["POST"])
def guardar_avisos():
    try:
        minutos = int(request.form.get("avisos_minutos") or 0)
    except ValueError:
        minutos = 10
    preferencias.guardar(
        avisos_rulo=request.form.get("avisos_rulo") == "on",
        avisos_minutos=max(0, min(minutos, 120)),
        avisos_pagos=request.form.get("avisos_pagos") == "on",
        avisos_agenda=request.form.get("avisos_agenda") == "on",
        segundo_plano=request.form.get("segundo_plano") == "on",
    )
    flash("Avisos guardados.", "success")
    return redirect(url_for("main.configuracion", _anchor="avisos"))


@bp.route("/preferencias/avisos/probar", methods=["POST"])
def probar_aviso():
    from .recordatorios import notificar

    ok = notificar(f"{APP_NAME}: aviso de prueba", "Así te va a avisar cuándo mover la plata entre billeteras.")
    flash("Te mandé un aviso de prueba." if ok else "Este sistema no permite mostrar avisos.", "success" if ok else "error")
    return redirect(url_for("main.configuracion", _anchor="avisos"))


@bp.route("/respaldo/descargar")
def descargar_respaldo():
    """Copia completa de la base, para guardar o llevar a otra PC."""
    destino = Path(tempfile.mkdtemp()) / f"{APP_ID}-respaldo-{date.today().isoformat()}.db"
    respaldo.exportar(destino)
    return send_file(destino, as_attachment=True, download_name=destino.name, mimetype="application/octet-stream")


@bp.route("/respaldo/restaurar", methods=["POST"])
def restaurar_respaldo():
    archivo = request.files.get("archivo")
    volver = url_for("main.configuracion", _anchor="respaldo")
    if not archivo or not archivo.filename:
        flash("Elegí un archivo de respaldo (.db).", "error")
        return redirect(volver)
    temporal = Path(tempfile.mkdtemp()) / "restaurar.db"
    archivo.save(temporal)
    try:
        respaldo.importar(temporal, motivo="antes_de_restaurar")
    except respaldo.RespaldoInvalido as exc:
        flash(str(exc), "error")
        return redirect(volver)
    if respaldo.carpeta_sync() is not None:
        respaldo.publicar()
    flash("Respaldo restaurado. Tus datos anteriores quedaron guardados en la carpeta de backups.", "success")
    return redirect(volver)


@bp.route("/respaldo/movimientos.csv")
def exportar_movimientos():
    """Todos los ingresos y gastos, mes a mes (desde el arranque hasta dentro
    de un año), en CSV para abrir con Excel (separador ; y coma decimal)."""
    conn = db.get_db()
    hoy = date.today()
    fechas = [f for _, f, _ in db.get_aportes(conn)] + [
        m.fecha for _, m in db.get_egresos(conn) + db.get_ingresos(conn) if m.fecha
    ]
    inicio = min(fechas + [hoy]).replace(day=1)
    fin = sumar_meses(hoy, 12)
    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=";")
    escritor.writerow(["Fecha", "Tipo", "Concepto", "Categoría", "Moneda", "Monto estimado", "Monto real", "Estado"])
    mes = inicio
    while mes <= fin:
        for f in _ocurrencias_mes(conn, mes.year, mes.month, hoy):
            escritor.writerow([
                f["fecha"].strftime("%d/%m/%Y"),
                "Ingreso" if f["tipo"] == "ingreso" else "Gasto",
                f["etiqueta"],
                f["categoria"] or ("" if f["tipo"] == "ingreso" else "Sin categoría"),
                f["moneda"],
                formato_ar(f["estimado"]),
                formato_ar(f["real"]) if f["real"] is not None else "",
                ("Cobrado" if f["tipo"] == "ingreso" else "Pagado") if f["real"] is not None else "Pendiente",
            ])
        mes = sumar_meses(mes, 1)
    return Response(
        "\ufeff" + salida.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={APP_ID}-movimientos-{hoy.isoformat()}.csv"},
    )


@bp.route("/respaldo/sync", methods=["POST"])
def guardar_sync():
    """Configura (o desactiva) la carpeta compartida para sincronizar."""
    volver = url_for("main.configuracion", _anchor="respaldo")
    carpeta = (request.form.get("carpeta") or "").strip()
    if not carpeta:
        preferencias.guardar(sync_carpeta="", sync_ultimo_hash="", sync_pendiente=False)
        flash("Sincronización desactivada.", "success")
        return redirect(volver)
    ruta = Path(carpeta).expanduser()
    if not ruta.is_dir():
        flash(f"No encuentro la carpeta {carpeta}. Revisá la ruta.", "error")
        return redirect(volver)
    preferencias.guardar(sync_carpeta=str(ruta), sync_ultimo_hash="", sync_pendiente=False)
    if (ruta / respaldo.ARCHIVO_SYNC).exists():
        mensaje = respaldo.traer_si_cambio(forzar=True)
        flash(mensaje or "Sincronización activada.", "success")
    else:
        respaldo.publicar()
        flash("Sincronización activada: tus datos quedaron en la carpeta compartida.", "success")
    return redirect(volver)


@bp.route("/respaldo/sync/ahora", methods=["POST"])
def sincronizar_ahora():
    mensaje = respaldo.traer_si_cambio(forzar=True)
    if mensaje is None or mensaje == "publicada":
        mensaje = "Todo sincronizado." if respaldo.publicar() else "No se pudo acceder a la carpeta compartida."
    flash(mensaje, "success")
    return redirect(url_for("main.configuracion", _anchor="respaldo"))


@bp.route("/mes")
def este_mes():
    hoy = date.today()
    try:
        anio, mes = (int(x) for x in request.args.get("mes", f"{hoy.year}-{hoy.month}").split("-"))
        date(anio, mes, 1)
        # Margen amplio pero acotado: los meses vecinos y los aguinaldos se
        # calculan con fechas que se salen del rango de `date` en los extremos.
        if abs(anio - hoy.year) > 100:
            raise ValueError
    except ValueError:
        anio, mes = hoy.year, hoy.month
    conn = db.get_db()
    filas = _ocurrencias_mes(conn, anio, mes, hoy)

    def total(tipo, campo):
        return sum((f["real"] if campo == "hecho" else f["estimado"]) for f in filas
                   if f["tipo"] == tipo and (campo != "hecho" or f["real"] is not None))

    hechos = [f for f in filas if f["real"] is not None]
    anterior = sumar_meses(date(anio, mes, 1), -1)
    siguiente = sumar_meses(date(anio, mes, 1), 1)
    return render_template(
        "mes.html",
        filas=filas,
        hoy=hoy,
        nombre_mes=f"{MESES[mes - 1]} {anio}",
        es_mes_actual=(anio, mes) == (hoy.year, hoy.month),
        mes_anterior=f"{anterior.year}-{anterior.month}",
        mes_siguiente=f"{siguiente.year}-{siguiente.month}",
        mes_param=f"{anio}-{mes}",
        ingresos_estimados=total("ingreso", "estimado"),
        ingresos_cobrados=total("ingreso", "hecho"),
        egresos_estimados=total("egreso", "estimado"),
        egresos_pagados=total("egreso", "hecho"),
        pendientes=sum(1 for f in filas if f["real"] is None),
        vencidos=sum(1 for f in filas if f["vencido"]),
        diferencia=sum((f["real"] - f["estimado"]) * (1 if f["tipo"] == "ingreso" else -1) for f in hechos),
    )


@bp.route("/mes/pago", methods=["POST"])
def registrar_pago():
    """Marca una ocurrencia como pagada/cobrada con su monto real."""
    tabla = {"egreso": "egresos", "ingreso": "ingresos"}.get(request.form.get("tipo"))
    volver = ruta_interna(request.form.get("volver"), url_for("main.este_mes", mes=request.form.get("mes")))
    try:
        movimiento_id = int(request.form["id"])
        fecha = date.fromisoformat(request.form["fecha"])
        clave = request.form.get("clave", "")
        monto = _numero(request.form["monto"])
    except (KeyError, ValueError):
        flash("Datos inválidos: revisá el monto.", "error")
        return redirect(volver)
    if tabla is None or clave not in ("", "aguinaldo", "aguinaldo_negro") or monto < 0:
        flash("Datos inválidos.", "error")
        return redirect(volver)
    db.guardar_pago(db.get_db(), tabla, movimiento_id, fecha, clave, monto)
    flash(("Cobro" if tabla == "ingresos" else "Pago") + f" registrado: {formato_pesos(monto)}.", "success")
    return redirect(volver)


@bp.route("/mes/pago/deshacer", methods=["POST"])
def deshacer_pago():
    tabla = {"egreso": "egresos", "ingreso": "ingresos"}.get(request.form.get("tipo"))
    volver = url_for("main.este_mes", mes=request.form.get("mes"))
    try:
        movimiento_id = int(request.form["id"])
        fecha = date.fromisoformat(request.form["fecha"])
    except (KeyError, ValueError):
        return redirect(volver)
    if tabla:
        db.eliminar_pago(db.get_db(), tabla, movimiento_id, fecha, request.form.get("clave", ""))
        flash("Volvió a pendiente: se usa el monto estimado.", "success")
    return redirect(volver)


@bp.route("/historial")
def historial():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        return redirect(url_for("main.configuracion", _anchor="aportes"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    ajustes = _ajustes_saldo(conn, aportes, egresos, ingresos, wallets)

    summaries = simulate(aportes, egresos, ingresos, date.today(), wallets, ajustes)
    capital_series = [
        {"date": s.date.isoformat(), "capital_cierre": s.capital_cierre} for s in summaries
    ]
    summaries.reverse()  # más reciente primero

    return render_template("history.html", summaries=summaries, capital_series=capital_series)


@bp.route("/historial/exportar.csv")
def exportar_historial():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        return redirect(url_for("main.configuracion", _anchor="aportes"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    ajustes = _ajustes_saldo(conn, aportes, egresos, ingresos, wallets)

    summaries = simulate(aportes, egresos, ingresos, date.today(), wallets, ajustes)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["fecha", "hora", "billetera", "monto_capturado", "tna", "rendimiento", "dias_acumulados"]
    )
    for s in summaries:
        for c in s.captures:
            writer.writerow(
                [
                    c.timestamp.date().isoformat(),
                    c.timestamp.strftime("%H:%M"),
                    c.wallet_name,
                    f"{c.monto_capturado:.2f}",
                    f"{c.tna:.2f}",
                    f"{c.rendimiento:.2f}",
                    c.dias_acumulados,
                ]
            )

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=rulo_historial.csv"},
    )


@bp.route("/billeteras")
def billeteras():
    return redirect(url_for("main.configuracion", _anchor="billeteras"))


@bp.route("/billeteras/<wallet_id>", methods=["POST"])
def actualizar_billetera(wallet_id):
    conn = db.get_db()
    try:
        tna = _numero(request.form["tna"], "la TNA")
        capture_time = _hora(request.form["capture_time"], "la hora de captura")
        payout_time = _hora(request.form["payout_time"], "la hora de acreditación")
        monto_minimo = _numero(request.form.get("monto_minimo") or 0, "el piso")
        monto_maximo_raw = request.form.get("monto_maximo")
        monto_maximo = _numero(monto_maximo_raw, "el techo") if monto_maximo_raw else None
    except KeyError:
        flash("Datos inválidos: revisá los campos de la billetera.", "error")
        return redirect(url_for("main.configuracion", _anchor="billeteras"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.configuracion", _anchor="billeteras"))

    if tna < 0:
        flash("La TNA no puede ser negativa.", "error")
        return redirect(url_for("main.configuracion", _anchor="billeteras"))
    if monto_minimo < 0:
        flash("El piso no puede ser negativo.", "error")
        return redirect(url_for("main.configuracion", _anchor="billeteras"))
    if monto_maximo is not None and monto_maximo <= monto_minimo:
        flash("El techo debe ser mayor al piso.", "error")
        return redirect(url_for("main.configuracion", _anchor="billeteras"))

    activo = request.form.get("activo") == "on"
    if db.update_wallet(
        conn, wallet_id, tna, capture_time, payout_time, activo, monto_minimo, monto_maximo
    ):
        flash("Billetera actualizada.", "success")
    else:
        flash("Esa billetera no existe.", "error")
    return redirect(url_for("main.configuracion", _anchor="billeteras"))


@bp.route("/graficos")
def graficos():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        return redirect(url_for("main.configuracion", _anchor="aportes"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    ajustes = _ajustes_saldo(conn, aportes, egresos, ingresos, wallets)

    summaries = simulate(aportes, egresos, ingresos, date.today(), wallets, ajustes)

    hoy = date.today()
    activas = [w for w in wallets if w.activo]
    tea_rulo = tea_del_rulo(summaries)
    tna_promedio = sum(w.default_tna for w in activas) / len(activas) if activas else 0.0

    return render_template(
        "graficos.html",
        tea_rulo=tea_rulo,
        tna_promedio=tna_promedio,
        flujo_mensual=_flujo_mensual(aportes, egresos, ingresos, hoy),
        mes_actual=f"{hoy.year}-{hoy.month:02d}",
    )


@bp.route("/proyeccion", methods=["GET", "POST"])
def proyeccion():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        return redirect(url_for("main.configuracion", _anchor="aportes"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    ajustes = _ajustes_saldo(conn, aportes, egresos, ingresos, wallets)

    hoy = date.today()
    default_objetivo = hoy + timedelta(days=30)

    aportes_planeados: list[tuple[date, float]] = []

    if request.method == "POST":
        try:
            fecha_objetivo = date.fromisoformat(request.form["fecha_objetivo"])
        except (KeyError, ValueError):
            flash("Fecha inválida.", "error")
            return redirect(url_for("main.proyeccion"))
        if fecha_objetivo <= hoy:
            flash("La fecha objetivo debe ser posterior a hoy.", "error")
            return redirect(url_for("main.proyeccion"))
        if fecha_objetivo > _fecha_limite_simulacion(hoy):
            flash(f"La proyección llega hasta {ANIOS_MAXIMOS_SIMULACION} años desde hoy.", "error")
            return redirect(url_for("main.proyeccion"))

        fechas_planeadas = request.form.getlist("aporte_futuro_fecha")
        montos_planeados = request.form.getlist("aporte_futuro_monto")
        for fecha_raw, monto_raw in zip(fechas_planeadas, montos_planeados):
            if not fecha_raw or not monto_raw:
                continue
            try:
                fecha_planeada = date.fromisoformat(fecha_raw)
                monto_planeado = _numero(monto_raw)
            except ValueError:
                flash("Uno de los aportes planeados tiene datos inválidos.", "error")
                return redirect(url_for("main.proyeccion"))
            if monto_planeado <= 0:
                flash("Los aportes planeados deben ser mayores a cero.", "error")
                return redirect(url_for("main.proyeccion"))
            if not (hoy < fecha_planeada <= fecha_objetivo):
                flash("Los aportes planeados deben caer entre hoy y la fecha objetivo.", "error")
                return redirect(url_for("main.proyeccion"))
            aportes_planeados.append((fecha_planeada, monto_planeado))
    else:
        fecha_objetivo = default_objetivo

    principal_total = sum(monto for _, monto in aportes)
    summaries = simulate(
        aportes + aportes_planeados, egresos, ingresos, fecha_objetivo, wallets, ajustes
    )
    capital_hoy = next((s.capital_cierre for s in summaries if s.date == hoy), principal_total)
    capital_final = summaries[-1].capital_cierre if summaries else principal_total

    # Comparación contra "dejar todo quieto" en la mejor billetera activa
    # (interés simple, sin rotar), con los mismos gastos e ingresos reales
    # descontados — para dimensionar la ventaja de rotar también a futuro.
    mejor_tna = max((w.default_tna for w in wallets if w.activo), default=0.0)
    capital_quieto = capital_simple(
        _con_ajustes(aportes, ajustes) + aportes_planeados, mejor_tna, fecha_objetivo, egresos, ingresos
    )
    ventaja_rulo_proyectada = capital_final - capital_quieto

    # Misma plata sin ningún interés: muestra cuánto aporta el rulo en sí.
    sin_rulo = capital_sin_rendimiento(_con_ajustes(aportes, ajustes) + aportes_planeados, egresos, ingresos, fecha_objetivo)
    capital_sin_rulo_final = sin_rulo.get(fecha_objetivo, principal_total)

    capital_series = [
        {
            "date": s.date.isoformat(),
            "capital_cierre": s.capital_cierre,
            "sin_rulo": sin_rulo.get(s.date, 0.0),
            "futuro": s.date > hoy,
        }
        for s in summaries
    ]

    # Puntos de control entre hoy y la fecha objetivo (~6 tramos), para mostrar
    # la ganancia numérica acumulada y por tramo en un gráfico de barras.
    capital_por_fecha = {s.date: s.capital_cierre for s in summaries}
    dias_totales = (fecha_objetivo - hoy).days
    n_tramos = min(8, max(1, dias_totales))
    paso = max(1, round(dias_totales / n_tramos))
    checkpoints = []
    d = hoy
    while d < fecha_objetivo:
        d = min(d + timedelta(days=paso), fecha_objetivo)
        checkpoints.append(d)
    if not checkpoints or checkpoints[-1] != fecha_objetivo:
        checkpoints.append(fecha_objetivo)

    ganancia_periodica = []
    capital_previo = capital_hoy
    for cp in checkpoints:
        capital_cp = capital_por_fecha.get(cp, capital_final)
        ganancia_periodica.append(
            {
                "date": cp.isoformat(),
                "ganancia_periodo": capital_cp - capital_previo,
                "ganancia_acumulada": capital_cp - capital_hoy,
            }
        )
        capital_previo = capital_cp

    return render_template(
        "proyeccion.html",
        fecha_objetivo=fecha_objetivo,
        hoy=hoy,
        capital_hoy=capital_hoy,
        capital_final=capital_final,
        rendimiento_proyectado=capital_final - capital_hoy,
        capital_series=capital_series,
        ganancia_periodica=ganancia_periodica,
        aportes_planeados=aportes_planeados,
        mejor_tna=mejor_tna,
        capital_quieto=capital_quieto,
        ventaja_rulo_proyectada=ventaja_rulo_proyectada,
        capital_sin_rulo_final=capital_sin_rulo_final,
        alertas=_alertas(conn, {s.date: s.capital_cierre for s in summaries}, hoy, egresos, ingresos),
        aporte_del_rulo=capital_final - capital_sin_rulo_final,
    )


@bp.route("/calculadora", methods=["GET", "POST"])
def calculadora():
    """Mini calculadora independiente: simula un único aporte rotando solo
    entre las billeteras elegidas, sin tocar los aportes/gastos reales
    guardados — para responder rápido "si meto $X el día Y, cuánto tengo
    el día Z" con la misma lógica de `simulate`."""
    conn = db.get_db()
    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    hoy = date.today()
    error = None

    if request.method == "POST":
        selected_ids = set(request.form.getlist("wallet_id"))
        try:
            monto = _numero(request.form.get("monto", ""))
            fecha_ingreso = date.fromisoformat(request.form["fecha_ingreso"])
            fecha_objetivo = date.fromisoformat(request.form["fecha_objetivo"])
        except (KeyError, ValueError):
            error = "Completá el monto y las fechas."
            monto, fecha_ingreso, fecha_objetivo = 0.0, hoy, hoy + timedelta(days=30)
        else:
            if monto <= 0:
                error = "El monto debe ser mayor a cero."
            elif fecha_objetivo <= fecha_ingreso:
                error = "La fecha objetivo debe ser posterior a la fecha de ingreso."
            elif (
                fecha_objetivo > _fecha_limite_simulacion(hoy)
                or fecha_objetivo > _fecha_limite_simulacion(fecha_ingreso)
            ):
                error = f"La simulación abarca como máximo {ANIOS_MAXIMOS_SIMULACION} años."
            elif not selected_ids:
                error = "Elegí al menos una billetera."
    else:
        selected_ids = {w.id for w in wallets if w.activo}
        monto = 100_000.0
        fecha_ingreso = hoy
        fecha_objetivo = hoy + timedelta(days=30)

    if error:
        flash(error, "error")

    # `activo=True` fuerza a incluir la billetera aunque esté desactivada en
    # la config real, y `reparto_socio_id=None` desactiva el split de fin de
    # semana: ese comportamiento depende de que su socio también esté en la
    # simulación, algo que esta calculadora no garantiza.
    calc_wallets = [
        replace(w, activo=True, reparto_socio_id=None) for w in wallets if w.id in selected_ids
    ]

    if calc_wallets and not error:
        summaries = simulate([(fecha_ingreso, monto)], [], [], fecha_objetivo, calc_wallets)
        capital_final = summaries[-1].capital_cierre if summaries else monto
        dias = (fecha_objetivo - fecha_ingreso).days
        tea = tasa_efectiva_anual(monto, capital_final, dias)
        serie = [{"date": s.date.isoformat(), "capital_cierre": s.capital_cierre} for s in summaries]
    else:
        capital_final = monto
        tea = 0.0
        serie = []

    return render_template(
        "calculadora.html",
        wallets=wallets,
        selected_ids=selected_ids,
        monto=monto,
        fecha_ingreso=fecha_ingreso,
        fecha_objetivo=fecha_objetivo,
        capital_final=capital_final,
        rendimiento=capital_final - monto,
        tea=tea,
        serie=serie,
    )


@bp.route("/recuperacion", methods=["GET", "POST"])
def recuperacion():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        return redirect(url_for("main.configuracion", _anchor="aportes"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    ajustes = _ajustes_saldo(conn, aportes, egresos, ingresos, wallets)

    hoy = date.today()
    inicio_rulo = min(fecha for fecha, _ in aportes)
    horizonte_anios = 5

    historial_hoy = simulate(aportes, egresos, ingresos, hoy, wallets, ajustes)
    capital_hoy = historial_hoy[-1].capital_cierre
    tea_lograda = tea_del_rulo(historial_hoy)

    etiqueta = ""
    monto_gasto = None
    fecha_gasto = hoy
    incluir_movimientos = False
    capital_base = None
    fecha_estimada = None
    dias_estimados = None
    ganancia_periodica = []

    if request.method == "POST":
        etiqueta = (request.form.get("etiqueta") or "").strip()
        incluir_movimientos = request.form.get("incluir_movimientos") == "on"
        try:
            monto_gasto = _numero(request.form["monto"])
            fecha_gasto = date.fromisoformat(request.form["fecha_gasto"])
        except (KeyError, ValueError):
            flash("Datos inválidos: revisá el monto y la fecha del gasto.", "error")
            return redirect(url_for("main.recuperacion"))
        if monto_gasto <= 0:
            flash("El monto debe ser mayor a cero.", "error")
            return redirect(url_for("main.recuperacion"))
        if fecha_gasto < inicio_rulo:
            flash("La fecha del gasto no puede ser anterior al inicio del rulo.", "error")
            return redirect(url_for("main.recuperacion"))
        if fecha_gasto > _fecha_limite_simulacion(hoy):
            flash(f"La fecha del gasto puede estar como máximo {ANIOS_MAXIMOS_SIMULACION} años adelante.", "error")
            return redirect(url_for("main.recuperacion"))

        # El capital al día del gasto es siempre un hecho real (con los
        # aportes, ingresos y egresos tal cual quedaron cargados) — el
        # checkbox de "cálculo crudo" solo afecta la proyección posterior,
        # no este punto de partida.
        historial = simulate(aportes, egresos, ingresos, max(fecha_gasto, hoy), wallets, ajustes)
        capital_base = next(s.capital_cierre for s in historial if s.date == fecha_gasto)

        # Desde el día del gasto se reinicia la simulación con ese capital
        # como único aporte, para proyectar "en limpio" hacia adelante: solo
        # el rendimiento propio del rulo, salvo que se pida sumar también los
        # ingresos/egresos ya configurados (sueldo, alquiler, etc.).
        horizonte = fecha_gasto + timedelta(days=365 * horizonte_anios)
        egresos_proy = egresos if incluir_movimientos else []
        ingresos_proy = ingresos if incluir_movimientos else []
        summaries_proy = simulate(
            [(fecha_gasto, capital_base)], egresos_proy, ingresos_proy, horizonte, wallets
        )

        fecha_estimada = fecha_recuperacion(summaries_proy, capital_base, monto_gasto, fecha_gasto)
        if fecha_estimada is not None:
            dias_estimados = (fecha_estimada - fecha_gasto).days

        limite = fecha_estimada or horizonte
        capital_por_fecha = {s.date: s.capital_cierre for s in summaries_proy}
        dias_ventana = (limite - fecha_gasto).days
        n_tramos = min(8, max(1, dias_ventana))
        paso = max(1, round(dias_ventana / n_tramos))
        checkpoints = []
        d = fecha_gasto
        while d < limite:
            d = min(d + timedelta(days=paso), limite)
            checkpoints.append(d)
        if not checkpoints or checkpoints[-1] != limite:
            checkpoints.append(limite)

        for cp in checkpoints:
            capital_cp = capital_por_fecha.get(cp, capital_base)
            ganancia_periodica.append(
                {"date": cp.isoformat(), "ganancia_acumulada": capital_cp - capital_base}
            )

    return render_template(
        "recuperacion.html",
        hoy=hoy,
        capital_hoy=capital_hoy,
        tea_lograda=tea_lograda,
        etiqueta=etiqueta,
        monto_gasto=monto_gasto,
        fecha_gasto=fecha_gasto,
        incluir_movimientos=incluir_movimientos,
        capital_base=capital_base,
        fecha_estimada=fecha_estimada,
        dias_estimados=dias_estimados,
        ganancia_periodica=ganancia_periodica,
        horizonte_anios=horizonte_anios,
    )


@bp.route("/patrimonio")
def patrimonio():
    conn = db.get_db()
    inversiones_rows = db.get_inversiones(conn)
    aportes = _aportes_pares(conn)

    capital_rulo = 0.0
    if aportes:
        wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
        egresos = _egresos(conn)
        ingresos = _ingresos(conn)
        ajustes = _ajustes_saldo(conn, aportes, egresos, ingresos, wallets)
        summaries = simulate(aportes, egresos, ingresos, date.today(), wallets, ajustes)
        capital_rulo = summaries[-1].capital_cierre if summaries else 0.0

    inversiones = [
        {
            "id": inversion_id,
            "etiqueta": etiqueta,
            "monto": monto,
            "tna": tna,
            "rendimiento_diario": daily_yield(monto, tna),
        }
        for inversion_id, etiqueta, monto, tna in inversiones_rows
    ]
    total_invertido = sum(inv["monto"] for inv in inversiones)
    rendimiento_diario_estimado = sum(inv["rendimiento_diario"] for inv in inversiones)

    return render_template(
        "patrimonio.html",
        inversiones=inversiones,
        total_invertido=total_invertido,
        rendimiento_diario_estimado=rendimiento_diario_estimado,
        capital_rulo=capital_rulo,
        patrimonio_total=capital_rulo + total_invertido,
    )


@bp.route("/patrimonio/agregar", methods=["POST"])
def agregar_inversion():
    conn = db.get_db()
    try:
        etiqueta, monto, tna = _parse_inversion_form(request.form)
    except KeyError:
        flash("Datos inválidos: revisá los campos de la inversión.", "error")
        return redirect(url_for("main.patrimonio"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.patrimonio"))

    db.add_inversion(conn, etiqueta, monto, tna)
    flash("Inversión agregada.", "success")
    return redirect(url_for("main.patrimonio"))


@bp.route("/patrimonio/editar/<int:inversion_id>", methods=["POST"])
def editar_inversion(inversion_id):
    conn = db.get_db()
    try:
        etiqueta, monto, tna = _parse_inversion_form(request.form)
    except KeyError:
        flash("Datos inválidos: revisá los campos de la inversión.", "error")
        return redirect(url_for("main.patrimonio"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.patrimonio"))

    if db.update_inversion(conn, inversion_id, etiqueta, monto, tna):
        flash("Inversión actualizada.", "success")
    else:
        flash("Esa inversión ya no existe.", "error")
    return redirect(url_for("main.patrimonio"))


@bp.route("/patrimonio/eliminar/<int:inversion_id>", methods=["POST"])
def eliminar_inversion(inversion_id):
    conn = db.get_db()
    db.delete_inversion(conn, inversion_id)
    flash("Inversión eliminada.", "success")
    return redirect(url_for("main.patrimonio"))
