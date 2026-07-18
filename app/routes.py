import csv
import io
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from . import db
from .domain import (
    Wallet,
    billetera_actual,
    capital_simple,
    proxima_captura,
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


@bp.route("/")
def dashboard():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        conn.close()
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    conn.close()

    ahora = datetime.now()
    hoy = ahora.date()
    summaries = simulate(aportes, hoy, wallets)
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
    conn.close()
    return render_template("configuracion.html", aportes=aportes)


@bp.route("/configuracion/eliminar/<int:aporte_id>", methods=["POST"])
def eliminar_aporte(aporte_id):
    conn = db.get_db()
    db.delete_aporte(conn, aporte_id)
    conn.close()
    flash("Aporte eliminado.")
    return redirect(url_for("main.configuracion"))


@bp.route("/historial")
def historial():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        conn.close()
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    conn.close()

    summaries = simulate(aportes, date.today(), wallets)
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
    conn.close()

    summaries = simulate(aportes, date.today(), wallets)

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
    conn = db.get_db()
    wallets = db.get_wallets(conn)
    conn.close()
    return render_template("wallets.html", wallets=wallets)


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
    return redirect(url_for("main.billeteras"))


@bp.route("/graficos")
def graficos():
    conn = db.get_db()
    aportes = _aportes_pares(conn)
    if not aportes:
        conn.close()
        return redirect(url_for("main.configuracion"))

    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    conn.close()

    summaries = simulate(aportes, date.today(), wallets)

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
    conn.close()

    hoy = date.today()
    default_objetivo = hoy + timedelta(days=30)

    if request.method == "POST":
        try:
            fecha_objetivo = date.fromisoformat(request.form["fecha_objetivo"])
        except (KeyError, ValueError):
            flash("Fecha inválida.")
            return redirect(url_for("main.proyeccion"))
        if fecha_objetivo <= hoy:
            flash("La fecha objetivo debe ser posterior a hoy.")
            return redirect(url_for("main.proyeccion"))
    else:
        fecha_objetivo = default_objetivo

    principal_total = sum(monto for _, monto in aportes)
    summaries = simulate(aportes, fecha_objetivo, wallets)
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
    )
