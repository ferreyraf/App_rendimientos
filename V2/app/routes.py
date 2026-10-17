import csv
import io
import json
import logging
import platform
import sys
import time
import urllib.request
from dataclasses import replace
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, url_for

from . import cotizacion, db, formato_ar
from .version import APP_NAME, DESCRIPTION, PUBLISHER, __version__
from .domain import (
    MovimientoRecurrente,
    Wallet,
    billetera_actual,
    calendario_ajustes,
    capital_simple,
    capital_sin_rendimiento,
    daily_yield,
    fecha_recuperacion,
    movimientos_del_mes,
    proxima_captura,
    proximo_vencimiento_impuestos,
    simulate,
    sumar_meses,
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
    return [m for _, m in db.get_ingresos(conn)]


# Categorías base de los egresos: fijos (alquiler, cuotas), servicios (luz,
# gas, internet), móviles (comida, transporte del día a día) y ocio. Se pueden
# sumar otras que ya existan en la base, pero estas siempre se ofrecen primero.
CATEGORIAS_GASTO = ("Gastos fijos", "Servicios", "Gastos móviles", "Ocio")

MESES = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)


def _agrupar_por_mes(movimientos, hoy: date) -> dict:
    """Separa los movimientos (id, MovimientoRecurrente) en recurrentes y
    puntuales agrupados por mes (más reciente primero). Cada mes trae el
    subtotal de sus puntuales y el total del mes sumando los recurrentes que
    caen en él — para que en Configuración se vea qué corresponde a cada mes."""
    recurrentes = [(id_, m) for id_, m in movimientos if m.recurrente]
    todos = [m for _, m in movimientos]

    por_mes: dict[tuple[int, int], list] = {}
    for id_, m in movimientos:
        if not m.recurrente and m.fecha is not None:
            por_mes.setdefault((m.fecha.year, m.fecha.month), []).append((id_, m))

    meses = []
    for (anio, mes) in sorted(por_mes, reverse=True):
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
    monto = float(form["monto"])
    if monto <= 0:
        raise ValueError("El monto debe ser mayor a cero.")
    tna = float(form.get("tna") or 0)
    if tna < 0:
        raise ValueError("La TNA no puede ser negativa.")
    return etiqueta, monto, tna


MONEDAS = ("ARS", "USD")


@bp.before_app_request
def _refrescar_cotizacion():
    if request.endpoint and request.endpoint != "static":
        cotizacion.refrescar_si_vieja(db.get_db())


def _parse_moneda(form, conn) -> str:
    """Moneda del formulario. Para cargar algo en dólares tiene que haber una
    cotización guardada (si no hay, se intenta consultar en el momento)."""
    moneda = form.get("moneda") or "ARS"
    if moneda not in MONEDAS:
        raise ValueError("Moneda inválida.")
    if moneda == "USD" and db.get_cotizacion(conn, "USD") is None and cotizacion.actualizar(conn) is None:
        raise ValueError("No se pudo obtener la cotización del dólar (infodolar). Probá de nuevo con conexión.")
    return moneda


def _parse_movimiento_form(
    form,
) -> tuple[str, float, bool, date | None, int | None, bool, str, float, date | None, int]:
    etiqueta = (form.get("etiqueta") or "").strip()
    if not etiqueta:
        raise ValueError("La etiqueta es obligatoria.")
    monto = float(form["monto"])
    if monto <= 0:
        raise ValueError("El monto debe ser mayor a cero.")
    categoria = (form.get("categoria") or "").strip()
    recurrente = form.get("recurrente") == "on"
    aumento_pct = 0.0
    aumento_inicio = None
    aumento_periodo_meses = 4
    if recurrente:
        dia_mes = int(form["dia_mes"])
        dia_habil = form.get("dia_habil") == "on"
        limite = 23 if dia_habil else 31
        if not (1 <= dia_mes <= limite):
            raise ValueError(f"El día {'hábil' if dia_habil else 'del mes'} debe estar entre 1 y {limite}.")
        # En un recurrente, `fecha` es el "desde" opcional (ver MovimientoRecurrente).
        desde_raw = (form.get("desde") or "").strip()
        fecha = date.fromisoformat(desde_raw) if desde_raw else None
        aumento_pct = float(form.get("aumento_pct") or 0)
        if aumento_pct < 0:
            raise ValueError("El aumento periódico no puede ser negativo.")
        aumento_periodo_meses = int(form.get("aumento_periodo_meses") or 4)
        if aumento_periodo_meses < 1:
            raise ValueError("El período del aumento debe ser de al menos 1 mes.")
        aumento_inicio_raw = (form.get("aumento_inicio") or "").strip()
        if aumento_pct > 0:
            if not aumento_inicio_raw:
                raise ValueError("Indicá desde cuándo se cuenta el aumento periódico.")
            aumento_inicio = date.fromisoformat(aumento_inicio_raw)
        elif aumento_inicio_raw:
            aumento_inicio = date.fromisoformat(aumento_inicio_raw)
    else:
        fecha = date.fromisoformat(form["fecha"])
        dia_mes = None
        dia_habil = False
    return (
        etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria,
        aumento_pct, aumento_inicio, aumento_periodo_meses,
    )


@bp.route("/")
def dashboard():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    total_invertido = _total_invertido(conn)

    ahora = datetime.now()
    hoy = ahora.date()
    summaries = simulate(aportes, egresos, ingresos, hoy, wallets)
    summary = summaries[-1] if summaries else None

    proxima_info = None
    proxima = proxima_captura(ahora, wallets)
    if proxima is not None:
        ts, wallet = proxima
        falta = ts - ahora
        horas, resto = divmod(int(falta.total_seconds()), 3600)
        minutos = resto // 60
        proxima_info = {
            "wallet_name": wallet.name,
            "timestamp": ts,
            "horas": horas,
            "minutos": minutos,
        }

    actual_info = None
    actual = billetera_actual(ahora, wallets)
    if actual is not None:
        ts, wallet = actual
        actual_info = {"wallet_name": wallet.name, "timestamp": ts}

    principal_total = sum(monto for _, monto in aportes)
    capital_hoy = summary.capital_cierre if summary else principal_total
    dias_totales = (hoy - min(fecha for fecha, _ in aportes)).days + 1

    ganancia_neta = capital_hoy - principal_total
    tea_lograda = tasa_efectiva_anual(principal_total, capital_hoy, dias_totales)

    mejor_quieto = max(
        (capital_simple(aportes, w.default_tna, hoy, egresos, ingresos) for w in wallets if w.activo),
        default=principal_total,
    )
    ventaja_rulo = capital_hoy - mejor_quieto

    total_aportado = principal_total
    total_egresos = sum(s.egreso_pagado for s in summaries)
    total_ingresos = sum(s.ingreso_recibido for s in summaries)
    patrimonio_total = capital_hoy + total_invertido

    vencimiento = proximo_vencimiento_impuestos(hoy)
    dias_para_vencimiento = (vencimiento - hoy).days

    egresos_mes = movimientos_del_mes(egresos, hoy.year, hoy.month)
    ingresos_mes = movimientos_del_mes(ingresos, hoy.year, hoy.month)
    total_egresos_mes = sum(m.monto for m in egresos_mes)
    total_ingresos_mes = sum(m.monto for m in ingresos_mes)

    egresos_por_categoria: dict[str, float] = {}
    for m in egresos_mes:
        clave = m.categoria or "Sin categoría"
        egresos_por_categoria[clave] = egresos_por_categoria.get(clave, 0.0) + m.monto
    egresos_por_categoria_serie = [
        {"categoria": k, "monto": v} for k, v in egresos_por_categoria.items()
    ]

    return render_template(
        "dashboard.html",
        summary=summary,
        hoy=hoy,
        ahora=ahora,
        proxima=proxima_info,
        actual=actual_info,
        ganancia_neta=ganancia_neta,
        tea_lograda=tea_lograda,
        ventaja_rulo=ventaja_rulo,
        total_aportado=total_aportado,
        total_egresos=total_egresos,
        total_ingresos=total_ingresos,
        total_invertido=total_invertido,
        patrimonio_total=patrimonio_total,
        vencimiento=vencimiento,
        dias_para_vencimiento=dias_para_vencimiento,
        total_egresos_mes=total_egresos_mes,
        total_ingresos_mes=total_ingresos_mes,
        egresos_por_categoria=egresos_por_categoria_serie,
    )


@bp.route("/configuracion", methods=["GET", "POST"])
def configuracion():
    conn = db.get_db()

    if request.method == "POST":
        try:
            monto = float(request.form["monto"])
            fecha = date.fromisoformat(request.form["fecha"])
        except (KeyError, ValueError):
            flash("Datos inválidos: revisá el monto y la fecha.", "error")
            return redirect(url_for("main.configuracion"))

        if monto <= 0:
            flash("El aporte debe ser mayor a cero.", "error")
            return redirect(url_for("main.configuracion"))
        if fecha > date.today():
            flash("La fecha del aporte no puede ser futura.", "error")
            return redirect(url_for("main.configuracion"))

        db.add_aporte(conn, fecha, monto)
        flash("Aporte agregado.", "success")
        return redirect(url_for("main.configuracion"))

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
        cotizacion_usd=db.get_cotizacion(conn, "USD"),
        total_aportes=sum(monto for _, _, monto in aportes),
        categorias_existentes=categorias_existentes,
    )


@bp.route("/configuracion/editar/<int:aporte_id>", methods=["POST"])
def editar_aporte(aporte_id):
    conn = db.get_db()
    try:
        monto = float(request.form["monto"])
        fecha = date.fromisoformat(request.form["fecha"])
    except (KeyError, ValueError):
        flash("Datos inválidos: revisá el monto y la fecha.", "error")
        return redirect(url_for("main.configuracion"))

    if monto <= 0:
        flash("El aporte debe ser mayor a cero.", "error")
        return redirect(url_for("main.configuracion"))
    if fecha > date.today():
        flash("La fecha del aporte no puede ser futura.", "error")
        return redirect(url_for("main.configuracion"))

    db.update_aporte(conn, aporte_id, fecha, monto)
    flash("Aporte actualizado.", "success")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/eliminar/<int:aporte_id>", methods=["POST"])
def eliminar_aporte(aporte_id):
    conn = db.get_db()
    db.delete_aporte(conn, aporte_id)
    flash("Aporte eliminado.", "success")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/egreso", methods=["POST"])
def agregar_egreso():
    conn = db.get_db()
    try:
        (
            etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria,
            aumento_pct, aumento_inicio, aumento_periodo_meses,
        ) = _parse_movimiento_form(request.form)
        moneda = _parse_moneda(request.form, conn)
    except KeyError:
        flash("Datos inválidos: revisá los campos del egreso.", "error")
        return redirect(url_for("main.configuracion"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.configuracion"))

    db.add_egreso(
        conn, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria,
        aumento_pct, aumento_inicio, aumento_periodo_meses, moneda,
    )
    flash("Egreso agregado.", "success")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/egreso/editar/<int:egreso_id>", methods=["POST"])
def editar_egreso(egreso_id):
    conn = db.get_db()
    try:
        (
            etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria,
            aumento_pct, aumento_inicio, aumento_periodo_meses,
        ) = _parse_movimiento_form(request.form)
        moneda = _parse_moneda(request.form, conn)
    except KeyError:
        flash("Datos inválidos: revisá los campos del egreso.", "error")
        return redirect(url_for("main.configuracion"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.configuracion"))

    db.update_egreso(
        conn, egreso_id, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria,
        aumento_pct, aumento_inicio, aumento_periodo_meses, moneda,
    )
    flash("Egreso actualizado.", "success")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/egreso/eliminar/<int:egreso_id>", methods=["POST"])
def eliminar_egreso(egreso_id):
    conn = db.get_db()
    db.delete_egreso(conn, egreso_id)
    flash("Egreso eliminado.", "success")
    return redirect(url_for("main.configuracion"))


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
        pct = float(request.form["pct"].replace(",", "."))
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
    return redirect(url_for("main.configuracion", _anchor="cotizacion"))


@bp.route("/acerca")
def acerca():
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
    )


@bp.route("/api/ipc")
def api_ipc():
    """IPC mensual (% de variación) de los últimos 36 meses, cacheado unas
    horas. Si la API no responde, devuelve 503 y la pantalla sigue andando
    sin las sugerencias."""
    ahora = time.time()
    if _ipc_cache["meses"] is None or ahora - _ipc_cache["timestamp"] > IPC_CACHE_SEGUNDOS:
        try:
            # Sin User-Agent propio la API responde 403 al de urllib.
            pedido = urllib.request.Request(IPC_URL, headers={"User-Agent": "RuloFinanciero/1.0"})
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
    except KeyError:
        flash("Datos inválidos: revisá los campos del ingreso.", "error")
        return redirect(url_for("main.configuracion"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.configuracion"))

    db.add_ingreso(
        conn, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil,
        aumento_pct, aumento_inicio, aumento_periodo_meses, moneda,
    )
    flash("Ingreso agregado.", "success")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/ingreso/editar/<int:ingreso_id>", methods=["POST"])
def editar_ingreso(ingreso_id):
    conn = db.get_db()
    try:
        (
            etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, _categoria,
            aumento_pct, aumento_inicio, aumento_periodo_meses,
        ) = _parse_movimiento_form(request.form)
        moneda = _parse_moneda(request.form, conn)
    except KeyError:
        flash("Datos inválidos: revisá los campos del ingreso.", "error")
        return redirect(url_for("main.configuracion"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.configuracion"))

    db.update_ingreso(
        conn, ingreso_id, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil,
        aumento_pct, aumento_inicio, aumento_periodo_meses, moneda,
    )
    flash("Ingreso actualizado.", "success")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/ingreso/eliminar/<int:ingreso_id>", methods=["POST"])
def eliminar_ingreso(ingreso_id):
    conn = db.get_db()
    db.delete_ingreso(conn, ingreso_id)
    flash("Ingreso eliminado.", "success")
    return redirect(url_for("main.configuracion"))


@bp.route("/historial")
def historial():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)

    summaries = simulate(aportes, egresos, ingresos, date.today(), wallets)
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
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)

    summaries = simulate(aportes, egresos, ingresos, date.today(), wallets)

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
    return redirect(url_for("main.configuracion"))


@bp.route("/billeteras/<wallet_id>", methods=["POST"])
def actualizar_billetera(wallet_id):
    conn = db.get_db()
    try:
        tna = float(request.form["tna"])
        capture_time = request.form["capture_time"]
        payout_time = request.form["payout_time"]
        monto_minimo = float(request.form.get("monto_minimo") or 0)
        monto_maximo_raw = request.form.get("monto_maximo")
        monto_maximo = float(monto_maximo_raw) if monto_maximo_raw else None
    except (KeyError, ValueError):
        flash("Datos inválidos: revisá los campos de la billetera.", "error")
        return redirect(url_for("main.configuracion"))

    if tna < 0:
        flash("La TNA no puede ser negativa.", "error")
        return redirect(url_for("main.configuracion"))
    if monto_minimo < 0:
        flash("El piso no puede ser negativo.", "error")
        return redirect(url_for("main.configuracion"))
    if monto_maximo is not None and monto_maximo <= monto_minimo:
        flash("El techo debe ser mayor al piso.", "error")
        return redirect(url_for("main.configuracion"))

    activo = request.form.get("activo") == "on"
    db.update_wallet(
        conn, wallet_id, tna, capture_time, payout_time, activo, monto_minimo, monto_maximo
    )
    flash("Billetera actualizada.", "success")
    return redirect(url_for("main.configuracion"))


@bp.route("/graficos")
def graficos():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)

    summaries = simulate(aportes, egresos, ingresos, date.today(), wallets)

    # Serie de capital + desglose aportado vs. ganancia generada, para el
    # área apilada "aportes vs. ganancia".
    aportes_ordenados = sorted(aportes, key=lambda a: a[0])
    capital_series = []
    aporte_acumulado = 0.0
    idx = 0
    for s in summaries:
        while idx < len(aportes_ordenados) and aportes_ordenados[idx][0] <= s.date:
            aporte_acumulado += aportes_ordenados[idx][1]
            idx += 1
        capital_series.append(
            {
                "date": s.date.isoformat(),
                "capital_cierre": s.capital_cierre,
                "aportado": aporte_acumulado,
                "ganancia": s.capital_cierre - aporte_acumulado,
            }
        )

    rendimiento_por_id = {w.id: 0.0 for w in wallets}
    for s in summaries:
        for c in s.captures:
            rendimiento_por_id[c.wallet_id] += c.rendimiento

    # Lista (no dict) para preservar el orden natural de las billeteras: Flask
    # serializa dicts con sort_keys=True en `tojson`, lo que reordenaría alfabéticamente.
    rendimiento_por_billetera = [
        {"nombre": w.name, "rendimiento": rendimiento_por_id[w.id]} for w in wallets
    ]

    # Comparación "quieto" (sin rotar, interés simple, sin capitalizar) contra
    # el resultado real y compuesto del rulo, para dimensionar la ventaja de rotar.
    hoy = date.today()
    principal_total = sum(monto for _, monto in aportes)
    dias_totales = (hoy - min(fecha for fecha, _ in aportes)).days + 1
    capital_final_rulo = summaries[-1].capital_cierre if summaries else principal_total
    comparacion = [
        {
            "nombre": w.name,
            "capital_final": capital_simple(aportes, w.default_tna, hoy, egresos, ingresos),
        }
        for w in wallets
        if w.activo
    ]
    comparacion.append({"nombre": "Rulo (real)", "capital_final": capital_final_rulo})

    activas = [w for w in wallets if w.activo]
    tea_rulo = tasa_efectiva_anual(principal_total, capital_final_rulo, dias_totales)
    tna_promedio = sum(w.default_tna for w in activas) / len(activas) if activas else 0.0

    return render_template(
        "graficos.html",
        capital_series=capital_series,
        rendimiento_por_billetera=rendimiento_por_billetera,
        comparacion=comparacion,
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
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)

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

        fechas_planeadas = request.form.getlist("aporte_futuro_fecha")
        montos_planeados = request.form.getlist("aporte_futuro_monto")
        for fecha_raw, monto_raw in zip(fechas_planeadas, montos_planeados):
            if not fecha_raw or not monto_raw:
                continue
            try:
                fecha_planeada = date.fromisoformat(fecha_raw)
                monto_planeado = float(monto_raw)
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
        aportes + aportes_planeados, egresos, ingresos, fecha_objetivo, wallets
    )
    capital_hoy = next((s.capital_cierre for s in summaries if s.date == hoy), principal_total)
    capital_final = summaries[-1].capital_cierre if summaries else principal_total

    # Comparación contra "dejar todo quieto" en la mejor billetera activa
    # (interés simple, sin rotar), con los mismos gastos e ingresos reales
    # descontados — para dimensionar la ventaja de rotar también a futuro.
    mejor_tna = max((w.default_tna for w in wallets if w.activo), default=0.0)
    capital_quieto = capital_simple(
        aportes + aportes_planeados, mejor_tna, fecha_objetivo, egresos, ingresos
    )
    ventaja_rulo_proyectada = capital_final - capital_quieto

    # Misma plata sin ningún interés: muestra cuánto aporta el rulo en sí.
    sin_rulo = capital_sin_rendimiento(aportes + aportes_planeados, egresos, ingresos, fecha_objetivo)
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
            monto = float(request.form.get("monto", ""))
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
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)

    hoy = date.today()
    inicio_rulo = min(fecha for fecha, _ in aportes)
    horizonte_anios = 5

    principal_total = sum(monto for _, monto in aportes)
    dias_totales = (hoy - inicio_rulo).days + 1
    capital_hoy = simulate(aportes, egresos, ingresos, hoy, wallets)[-1].capital_cierre
    tea_lograda = tasa_efectiva_anual(principal_total, capital_hoy, dias_totales)

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
            monto_gasto = float(request.form["monto"])
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

        # El capital al día del gasto es siempre un hecho real (con los
        # aportes, ingresos y egresos tal cual quedaron cargados) — el
        # checkbox de "cálculo crudo" solo afecta la proyección posterior,
        # no este punto de partida.
        historial = simulate(aportes, egresos, ingresos, max(fecha_gasto, hoy), wallets)
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
        summaries = simulate(aportes, egresos, ingresos, date.today(), wallets)
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

    db.update_inversion(conn, inversion_id, etiqueta, monto, tna)
    flash("Inversión actualizada.", "success")
    return redirect(url_for("main.patrimonio"))


@bp.route("/patrimonio/eliminar/<int:inversion_id>", methods=["POST"])
def eliminar_inversion(inversion_id):
    conn = db.get_db()
    db.delete_inversion(conn, inversion_id)
    flash("Inversión eliminada.", "success")
    return redirect(url_for("main.patrimonio"))
