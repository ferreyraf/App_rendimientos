from datetime import date

from flask import Blueprint, redirect, render_template, request, url_for

from . import db
from .domain import Wallet, active_wallets_for_date, daily_yield

bp = Blueprint("main", __name__)


def _wallet_from_row(row):
    return Wallet(
        id=row["id"],
        name=row["name"],
        capture_time=row["capture_time"],
        active_weekdays=tuple(int(x) for x in row["active_weekdays"].split(",")),
        default_tna=row["tna"],
    )


@bp.route("/")
def dashboard():
    conn = db.get_db()
    today = date.today()
    all_wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
    active = active_wallets_for_date(today, all_wallets)
    captures_today = {c["wallet_id"]: c for c in db.get_captures_for_date(conn, today.isoformat())}
    total_hoy = sum(c["rendimiento"] for c in captures_today.values())
    conn.close()
    return render_template(
        "dashboard.html",
        today=today,
        active_wallets=active,
        captures_today=captures_today,
        total_hoy=total_hoy,
    )


@bp.route("/capturar", methods=["POST"])
def capturar():
    conn = db.get_db()
    wallet_id = request.form["wallet_id"]
    fecha = request.form.get("fecha") or date.today().isoformat()
    monto = float(request.form["monto"])
    wallet_row = db.get_wallet(conn, wallet_id)
    tna = wallet_row["tna"]
    rendimiento = daily_yield(monto, tna)
    db.upsert_capture(conn, wallet_id, fecha, monto, tna, rendimiento)
    conn.close()
    return redirect(url_for("main.dashboard"))


@bp.route("/historial")
def historial():
    conn = db.get_db()
    captures = db.get_all_captures(conn)
    total_rendimiento = sum(c["rendimiento"] for c in captures)
    conn.close()
    return render_template("history.html", captures=captures, total_rendimiento=total_rendimiento)


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
    db.update_wallet_tna(conn, wallet_id, tna)
    conn.close()
    return redirect(url_for("main.billeteras"))
