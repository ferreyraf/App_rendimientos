import csv
import io
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from . import db
from .domain import Wallet, proxima_captura, simulate

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
    )


@bp.route("/")
def dashboard():
    conn = db.get_db()
    config = db.get_config(conn)
    if config is None:
        conn.close()
        return redirect(url_for("main.configuracion"))

    principal, start_date = config
    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    conn.close()

    ahora = datetime.now()
    hoy = ahora.date()
    summaries = simulate(start_date, hoy, principal, wallets)
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

    return render_template(
        "dashboard.html", summary=summary, hoy=hoy, ahora=ahora, proxima=proxima_info
    )


@bp.route("/configuracion", methods=["GET", "POST"])
def configuracion():
    conn = db.get_db()

    if request.method == "POST":
        try:
            principal = float(request.form["principal"])
            start_date = date.fromisoformat(request.form["start_date"])
        except (KeyError, ValueError):
            conn.close()
            flash("Datos inválidos: revisá el capital inicial y la fecha.")
            return redirect(url_for("main.configuracion"))

        if principal <= 0:
            conn.close()
            flash("El capital inicial debe ser mayor a cero.")
            return redirect(url_for("main.configuracion"))
        if start_date > date.today():
            conn.close()
            flash("La fecha de inicio no puede ser futura.")
            return redirect(url_for("main.configuracion"))

        db.set_config(conn, principal, start_date)
        conn.close()
        flash("Configuración guardada.")
        return redirect(url_for("main.dashboard"))

    config = db.get_config(conn)
    conn.close()
    return render_template("configuracion.html", config=config)


@bp.route("/historial")
def historial():
    conn = db.get_db()
    config = db.get_config(conn)
    if config is None:
        conn.close()
        return redirect(url_for("main.configuracion"))

    principal, start_date = config
    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    conn.close()

    summaries = simulate(start_date, date.today(), principal, wallets)
    summaries.reverse()  # más reciente primero

    return render_template("history.html", summaries=summaries)


@bp.route("/historial/exportar.csv")
def exportar_historial():
    conn = db.get_db()
    config = db.get_config(conn)
    if config is None:
        conn.close()
        return redirect(url_for("main.configuracion"))

    principal, start_date = config
    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    conn.close()

    summaries = simulate(start_date, date.today(), principal, wallets)

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
    db.update_wallet(conn, wallet_id, tna, capture_time, payout_time, activo)
    conn.close()
    return redirect(url_for("main.billeteras"))


@bp.route("/graficos")
def graficos():
    conn = db.get_db()
    config = db.get_config(conn)
    if config is None:
        conn.close()
        return redirect(url_for("main.configuracion"))

    principal, start_date = config
    wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    conn.close()

    summaries = simulate(start_date, date.today(), principal, wallets)

    capital_series = [
        {"date": s.date.isoformat(), "capital_cierre": s.capital_cierre} for s in summaries
    ]

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
    dias_totales = (date.today() - start_date).days + 1
    capital_final_rulo = summaries[-1].capital_cierre if summaries else principal
    comparacion = [
        {
            "nombre": w.name,
            "capital_final": principal + principal * (w.default_tna / 100) * dias_totales / 365,
        }
        for w in wallets
        if w.activo
    ]
    comparacion.append({"nombre": "Rulo (real)", "capital_final": capital_final_rulo})

    return render_template(
        "graficos.html",
        capital_series=capital_series,
        rendimiento_por_billetera=rendimiento_por_billetera,
        comparacion=comparacion,
    )


@bp.route("/proyeccion", methods=["GET", "POST"])
def proyeccion():
    conn = db.get_db()
    config = db.get_config(conn)
    if config is None:
        conn.close()
        return redirect(url_for("main.configuracion"))

    principal, start_date = config
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

    summaries = simulate(start_date, fecha_objetivo, principal, wallets)
    capital_hoy = next((s.capital_cierre for s in summaries if s.date == hoy), principal)
    capital_final = summaries[-1].capital_cierre if summaries else principal

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
