import csv
import io
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from . import db
from .domain import (
    MovimientoRecurrente,
    Wallet,
    billetera_actual,
    capital_simple,
    daily_yield,
    movimientos_del_mes,
    proxima_captura,
    proximo_vencimiento_impuestos,
    simulate,
    tasa_efectiva_anual,
)

bp = Blueprint("main", __name__)


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


def _parse_movimiento_form(form) -> tuple[str, float, bool, date | None, int | None, bool, str]:
    etiqueta = (form.get("etiqueta") or "").strip()
    if not etiqueta:
        raise ValueError("La etiqueta es obligatoria.")
    monto = float(form["monto"])
    if monto <= 0:
        raise ValueError("El monto debe ser mayor a cero.")
    categoria = (form.get("categoria") or "").strip()
    recurrente = form.get("recurrente") == "on"
    if recurrente:
        dia_mes = int(form["dia_mes"])
        dia_habil = form.get("dia_habil") == "on"
        limite = 23 if dia_habil else 31
        if not (1 <= dia_mes <= limite):
            raise ValueError(f"El día {'hábil' if dia_habil else 'del mes'} debe estar entre 1 y {limite}.")
        fecha = None
    else:
        fecha = date.fromisoformat(form["fecha"])
        dia_mes = None
        dia_habil = False
    return etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria


@bp.route("/")
def dashboard():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        conn.close()
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    total_invertido = _total_invertido(conn)
    conn.close()

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
        (capital_simple(aportes, w.default_tna, hoy) for w in wallets if w.activo),
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
            conn.close()
            flash("Datos inválidos: revisá el monto y la fecha.")
            return redirect(url_for("main.configuracion"))

        if monto <= 0:
            conn.close()
            flash("El aporte debe ser mayor a cero.")
            return redirect(url_for("main.configuracion"))
        if fecha > date.today():
            conn.close()
            flash("La fecha del aporte no puede ser futura.")
            return redirect(url_for("main.configuracion"))

        db.add_aporte(conn, fecha, monto)
        conn.close()
        flash("Aporte agregado.")
        return redirect(url_for("main.configuracion"))

    aportes = db.get_aportes(conn)
    wallets = db.get_wallets(conn)
    egresos = db.get_egresos(conn)
    ingresos = db.get_ingresos(conn)
    conn.close()
    total_egresos = sum(m.monto for _, m in egresos)
    total_ingresos = sum(m.monto for _, m in ingresos)
    categorias_existentes = sorted({m.categoria for _, m in egresos if m.categoria})
    return render_template(
        "configuracion.html",
        aportes=aportes,
        wallets=wallets,
        egresos=egresos,
        ingresos=ingresos,
        total_egresos=total_egresos,
        total_ingresos=total_ingresos,
        categorias_existentes=categorias_existentes,
    )


@bp.route("/configuracion/editar/<int:aporte_id>", methods=["POST"])
def editar_aporte(aporte_id):
    conn = db.get_db()
    try:
        monto = float(request.form["monto"])
        fecha = date.fromisoformat(request.form["fecha"])
    except (KeyError, ValueError):
        conn.close()
        flash("Datos inválidos: revisá el monto y la fecha.")
        return redirect(url_for("main.configuracion"))

    if monto <= 0:
        conn.close()
        flash("El aporte debe ser mayor a cero.")
        return redirect(url_for("main.configuracion"))
    if fecha > date.today():
        conn.close()
        flash("La fecha del aporte no puede ser futura.")
        return redirect(url_for("main.configuracion"))

    db.update_aporte(conn, aporte_id, fecha, monto)
    conn.close()
    flash("Aporte actualizado.")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/eliminar/<int:aporte_id>", methods=["POST"])
def eliminar_aporte(aporte_id):
    conn = db.get_db()
    db.delete_aporte(conn, aporte_id)
    conn.close()
    flash("Aporte eliminado.")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/egreso", methods=["POST"])
def agregar_egreso():
    conn = db.get_db()
    try:
        etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria = _parse_movimiento_form(request.form)
    except KeyError:
        conn.close()
        flash("Datos inválidos: revisá los campos del egreso.")
        return redirect(url_for("main.configuracion"))
    except ValueError as exc:
        conn.close()
        flash(str(exc))
        return redirect(url_for("main.configuracion"))

    db.add_egreso(conn, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria)
    conn.close()
    flash("Egreso agregado.")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/egreso/editar/<int:egreso_id>", methods=["POST"])
def editar_egreso(egreso_id):
    conn = db.get_db()
    try:
        etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria = _parse_movimiento_form(request.form)
    except KeyError:
        conn.close()
        flash("Datos inválidos: revisá los campos del egreso.")
        return redirect(url_for("main.configuracion"))
    except ValueError as exc:
        conn.close()
        flash(str(exc))
        return redirect(url_for("main.configuracion"))

    db.update_egreso(conn, egreso_id, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, categoria)
    conn.close()
    flash("Egreso actualizado.")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/egreso/eliminar/<int:egreso_id>", methods=["POST"])
def eliminar_egreso(egreso_id):
    conn = db.get_db()
    db.delete_egreso(conn, egreso_id)
    conn.close()
    flash("Egreso eliminado.")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/ingreso", methods=["POST"])
def agregar_ingreso():
    conn = db.get_db()
    try:
        etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, _categoria = _parse_movimiento_form(request.form)
    except KeyError:
        conn.close()
        flash("Datos inválidos: revisá los campos del ingreso.")
        return redirect(url_for("main.configuracion"))
    except ValueError as exc:
        conn.close()
        flash(str(exc))
        return redirect(url_for("main.configuracion"))

    db.add_ingreso(conn, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil)
    conn.close()
    flash("Ingreso agregado.")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/ingreso/editar/<int:ingreso_id>", methods=["POST"])
def editar_ingreso(ingreso_id):
    conn = db.get_db()
    try:
        etiqueta, monto, recurrente, fecha, dia_mes, dia_habil, _categoria = _parse_movimiento_form(request.form)
    except KeyError:
        conn.close()
        flash("Datos inválidos: revisá los campos del ingreso.")
        return redirect(url_for("main.configuracion"))
    except ValueError as exc:
        conn.close()
        flash(str(exc))
        return redirect(url_for("main.configuracion"))

    db.update_ingreso(conn, ingreso_id, etiqueta, monto, recurrente, fecha, dia_mes, dia_habil)
    conn.close()
    flash("Ingreso actualizado.")
    return redirect(url_for("main.configuracion"))


@bp.route("/configuracion/ingreso/eliminar/<int:ingreso_id>", methods=["POST"])
def eliminar_ingreso(ingreso_id):
    conn = db.get_db()
    db.delete_ingreso(conn, ingreso_id)
    conn.close()
    flash("Ingreso eliminado.")
    return redirect(url_for("main.configuracion"))


@bp.route("/historial")
def historial():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        conn.close()
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    conn.close()

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
        conn.close()
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    conn.close()

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
    tna = float(request.form["tna"])
    capture_time = request.form["capture_time"]
    payout_time = request.form["payout_time"]
    activo = request.form.get("activo") == "on"
    monto_minimo = float(request.form.get("monto_minimo") or 0)
    monto_maximo_raw = request.form.get("monto_maximo")
    monto_maximo = float(monto_maximo_raw) if monto_maximo_raw else None
    db.update_wallet(
        conn, wallet_id, tna, capture_time, payout_time, activo, monto_minimo, monto_maximo
    )
    conn.close()
    return redirect(url_for("main.configuracion"))


@bp.route("/graficos")
def graficos():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        conn.close()
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    conn.close()

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
        {"nombre": w.name, "capital_final": capital_simple(aportes, w.default_tna, hoy)}
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
    )


@bp.route("/proyeccion", methods=["GET", "POST"])
def proyeccion():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        conn.close()
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    egresos = _egresos(conn)
    ingresos = _ingresos(conn)
    conn.close()

    hoy = date.today()
    default_objetivo = hoy + timedelta(days=30)

    aportes_planeados: list[tuple[date, float]] = []

    if request.method == "POST":
        try:
            fecha_objetivo = date.fromisoformat(request.form["fecha_objetivo"])
        except (KeyError, ValueError):
            flash("Fecha inválida.")
            return redirect(url_for("main.proyeccion"))
        if fecha_objetivo <= hoy:
            flash("La fecha objetivo debe ser posterior a hoy.")
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
                flash("Uno de los aportes planeados tiene datos inválidos.")
                return redirect(url_for("main.proyeccion"))
            if monto_planeado <= 0:
                flash("Los aportes planeados deben ser mayores a cero.")
                return redirect(url_for("main.proyeccion"))
            if not (hoy < fecha_planeada <= fecha_objetivo):
                flash("Los aportes planeados deben caer entre hoy y la fecha objetivo.")
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

    capital_series = [
        {"date": s.date.isoformat(), "capital_cierre": s.capital_cierre, "futuro": s.date > hoy}
        for s in summaries
    ]

    return render_template(
        "proyeccion.html",
        fecha_objetivo=fecha_objetivo,
        hoy=hoy,
        capital_hoy=capital_hoy,
        capital_final=capital_final,
        rendimiento_proyectado=capital_final - capital_hoy,
        capital_series=capital_series,
        aportes_planeados=aportes_planeados,
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
    conn.close()

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
        conn.close()
        flash("Datos inválidos: revisá los campos de la inversión.")
        return redirect(url_for("main.patrimonio"))
    except ValueError as exc:
        conn.close()
        flash(str(exc))
        return redirect(url_for("main.patrimonio"))

    db.add_inversion(conn, etiqueta, monto, tna)
    conn.close()
    flash("Inversión agregada.")
    return redirect(url_for("main.patrimonio"))


@bp.route("/patrimonio/editar/<int:inversion_id>", methods=["POST"])
def editar_inversion(inversion_id):
    conn = db.get_db()
    try:
        etiqueta, monto, tna = _parse_inversion_form(request.form)
    except KeyError:
        conn.close()
        flash("Datos inválidos: revisá los campos de la inversión.")
        return redirect(url_for("main.patrimonio"))
    except ValueError as exc:
        conn.close()
        flash(str(exc))
        return redirect(url_for("main.patrimonio"))

    db.update_inversion(conn, inversion_id, etiqueta, monto, tna)
    conn.close()
    flash("Inversión actualizada.")
    return redirect(url_for("main.patrimonio"))


@bp.route("/patrimonio/eliminar/<int:inversion_id>", methods=["POST"])
def eliminar_inversion(inversion_id):
    conn = db.get_db()
    db.delete_inversion(conn, inversion_id)
    conn.close()
    flash("Inversión eliminada.")
    return redirect(url_for("main.patrimonio"))
