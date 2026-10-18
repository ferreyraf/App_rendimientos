"""Pantallas de Rutina: hábitos (checklist del día, rachas, cumplimiento) y
tareas con fecha límite. También arma lo que Rutina aporta a la pantalla Hoy.

Tildar un hábito o completar una tarea no deja mensaje (se hace muchas
veces y la pantalla ya muestra el cambio); crear, editar o borrar sí.
"""

import calendar
import json
from datetime import date, datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import db
from ..routes import MESES, _entero, _fecha, _hora, _numero, ruta_interna
from . import datos
from .logica import (
    ANIMOS,
    CALIDADES,
    DIAS_CORTOS,
    PRIORIDADES,
    clasificar_tareas,
    cumplimiento,
    horas_de_sueno,
    media_movil,
    mejor_racha,
    porcentaje,
    progreso_del_dia,
    promedio,
    racha,
    valor_hace,
)

bp = Blueprint("rutina", __name__, url_prefix="/rutina")

MAX_NOMBRE = 60
MAX_TITULO = 120
MAX_NOTAS = 1000


def _fecha_corta(fecha: date, hoy: date) -> str:
    if fecha == hoy:
        return "Hoy"
    if fecha == hoy + timedelta(days=1):
        return "Mañana"
    if fecha == hoy - timedelta(days=1):
        return "Ayer"
    return fecha.strftime("%d/%m")


# --- Hábitos -----------------------------------------------------------------

def items_checklist(conn, usuario: int, fecha: date, hoy: date) -> list[dict]:
    """Hábitos que aplican `fecha`, con lo hecho ese día y la racha."""
    habitos = [h for h in datos.get_habitos(conn, usuario) if h.aplica(fecha)]
    if not habitos:
        return []
    desde = min(h.desde for h in habitos)
    cantidades = datos.get_cantidades(conn, usuario, desde, hoy)
    return [
        {
            "h": h,
            "cantidad": cantidades.get((h.id, fecha), 0),
            "hecho": h.hecho(cantidades, fecha),
            "racha": racha(h, cantidades, hoy),
        }
        for h in habitos
    ]


@bp.route("/habitos")
def habitos():
    conn = db.get_db()
    usuario = db.usuario_actual()
    hoy = date.today()
    try:
        fecha = date.fromisoformat(request.args.get("fecha", ""))
    except ValueError:
        fecha = hoy
    fecha = min(fecha, hoy)

    todos = datos.get_habitos(conn, usuario)
    if not todos:
        return render_template("rutina/habitos.html", sin_habitos=True, fecha=hoy, hoy=hoy)

    desde = min(h.desde for h in todos)
    cantidades = datos.get_cantidades(conn, usuario, min(desde, fecha.replace(day=1)), hoy)
    hechos_dia, total_dia = progreso_del_dia(todos, cantidades, fecha)

    rachas = sorted(
        ({"h": h, "actual": racha(h, cantidades, hoy), "mejor": mejor_racha(h, cantidades, hoy),
          "pct30": porcentaje(*cumplimiento([h], cantidades, hoy - timedelta(days=29), hoy, hoy))} for h in todos),
        key=lambda r: -r["actual"],
    )
    lunes = hoy - timedelta(days=hoy.weekday())
    pct_semana = porcentaje(*cumplimiento(todos, cantidades, lunes, hoy, hoy))
    pct_30 = porcentaje(*cumplimiento(todos, cantidades, hoy - timedelta(days=29), hoy, hoy))

    # Semana (lunes a domingo) que contiene la fecha elegida.
    inicio_semana = fecha - timedelta(days=fecha.weekday())
    dias_semana = [inicio_semana + timedelta(days=i) for i in range(7)]
    semana = [
        {"h": h, "celdas": [
            {"fecha": d, "aplica": h.aplica(d), "hecho": h.hecho(cantidades, d), "futuro": d > hoy}
            for d in dias_semana
        ]}
        for h in todos
    ]

    # Calendario del mes de la fecha elegida, con el % de cada día.
    primero = fecha.replace(day=1)
    _, n_dias = calendar.monthrange(fecha.year, fecha.month)
    celdas_mes = [None] * primero.weekday()
    for n in range(1, n_dias + 1):
        d = primero.replace(day=n)
        hechos, total = progreso_del_dia(todos, cantidades, d)
        celdas_mes.append({
            "fecha": d,
            "pct": None if d > hoy or not total else hechos * 100 // total,
            "texto": f"{hechos}/{total}" if total else "",
        })

    return render_template(
        "rutina/habitos.html",
        sin_habitos=False,
        hoy=hoy,
        fecha=fecha,
        nombre_fecha=_fecha_corta(fecha, hoy) if abs((hoy - fecha).days) <= 1 else fecha.strftime("%d/%m/%Y"),
        anterior=(fecha - timedelta(days=1)).isoformat(),
        siguiente=(fecha + timedelta(days=1)).isoformat() if fecha < hoy else None,
        items=items_checklist(conn, usuario, fecha, hoy),
        volver=request.full_path.rstrip("?"),
        hechos_dia=hechos_dia,
        total_dia=total_dia,
        mejor_activa=rachas[0] if rachas and rachas[0]["actual"] else None,
        pct_semana=pct_semana,
        pct_30=pct_30,
        rachas=rachas,
        dias_semana=dias_semana,
        dias_cortos=DIAS_CORTOS,
        semana=semana,
        celdas_mes=celdas_mes,
        nombre_mes=f"{MESES[fecha.month - 1]} {fecha.year}",
    )


@bp.route("/habitos/<int:habito_id>/marcar", methods=["POST"])
def marcar_habito(habito_id):
    """Tilda/destilda (meta 1) o suma/resta una vez (meta mayor) un día."""
    conn = db.get_db()
    hoy = date.today()
    volver = ruta_interna(request.form.get("volver"), url_for("rutina.habitos"))
    habito = datos.get_habito(conn, db.usuario_actual(), habito_id)
    try:
        fecha = date.fromisoformat(request.form.get("fecha") or hoy.isoformat())
    except ValueError:
        fecha = None
    if habito is None or fecha is None or fecha > hoy or not habito.aplica(fecha):
        flash("No se pudo marcar ese hábito para ese día.", "error")
        return redirect(volver)

    actual = datos.get_cantidades(conn, db.usuario_actual(), fecha, fecha).get((habito.id, fecha), 0)
    accion = request.form.get("accion", "alternar")
    if accion == "sumar":
        nueva = min(actual + 1, habito.meta * 10)
    elif accion == "restar":
        nueva = max(actual - 1, 0)
    else:
        nueva = 0 if actual >= habito.meta else habito.meta
    datos.set_cantidad(conn, habito.id, fecha, nueva)
    return redirect(volver)


def _parse_habito(form) -> tuple:
    nombre = (form.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("Poné un nombre para el hábito.")
    if len(nombre) > MAX_NOMBRE:
        raise ValueError(f"El nombre puede tener hasta {MAX_NOMBRE} caracteres.")
    icono = (form.get("icono") or "").strip()[:8]
    dias = sorted({_entero(d, "los días") for d in form.getlist("dias")})
    if not dias or any(not 0 <= d <= 6 for d in dias):
        raise ValueError("Elegí al menos un día de la semana.")
    meta = _entero(form.get("meta") or 1, "la meta diaria")
    if not 1 <= meta <= 100:
        raise ValueError("La meta diaria tiene que estar entre 1 y 100.")
    unidad = (form.get("unidad") or "").strip()[:20]
    desde_raw = (form.get("desde") or "").strip()
    desde = _fecha(desde_raw, "la fecha de inicio") if desde_raw else date.today()
    return nombre, icono, dias, meta, unidad, desde


def _datos_edicion_habito(h) -> str:
    return json.dumps({
        "titulo": h.nombre,
        "url_editar": url_for("rutina.editar_habito", habito_id=h.id),
        "url_eliminar": url_for("rutina.eliminar_habito", habito_id=h.id),
        "url_archivar": url_for("rutina.archivar_habito", habito_id=h.id),
        "texto_archivar": "Reactivar" if not h.activo else "Archivar",
        "campos": {
            "nombre": h.nombre, "icono": h.icono, "dias": list(h.dias), "meta": h.meta,
            "unidad": h.unidad, "desde": h.desde.isoformat(),
        },
    }, ensure_ascii=False)


@bp.route("/habitos/gestionar")
def habitos_gestionar():
    conn = db.get_db()
    todos = datos.get_habitos(conn, db.usuario_actual(), incluir_archivados=True)
    return render_template(
        "rutina/habitos_gestionar.html",
        activos=[(h, _datos_edicion_habito(h)) for h in todos if h.activo],
        archivados=[(h, _datos_edicion_habito(h)) for h in todos if not h.activo],
        dias_cortos=DIAS_CORTOS,
        hoy=date.today(),
    )


@bp.route("/habitos", methods=["POST"])
def crear_habito():
    try:
        valores = _parse_habito(request.form)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        datos.add_habito(db.get_db(), db.usuario_actual(), *valores)
        flash(f"Hábito \"{valores[0]}\" agregado.", "success")
    return redirect(url_for("rutina.habitos_gestionar"))


@bp.route("/habitos/<int:habito_id>/editar", methods=["POST"])
def editar_habito(habito_id):
    try:
        valores = _parse_habito(request.form)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        if datos.update_habito(db.get_db(), db.usuario_actual(), habito_id, *valores):
            flash("Hábito actualizado.", "success")
        else:
            flash("Ese hábito ya no existe.", "error")
    return redirect(url_for("rutina.habitos_gestionar"))


@bp.route("/habitos/<int:habito_id>/archivar", methods=["POST"])
def archivar_habito(habito_id):
    """Archivar lo saca del día a día sin perder su historial (o lo reactiva)."""
    conn = db.get_db()
    habito = datos.get_habito(conn, db.usuario_actual(), habito_id)
    if habito is None:
        flash("Ese hábito ya no existe.", "error")
    else:
        datos.set_habito_activo(conn, db.usuario_actual(), habito_id, not habito.activo)
        flash(f"Hábito \"{habito.nombre}\" {'archivado' if habito.activo else 'reactivado'}.", "success")
    return redirect(url_for("rutina.habitos_gestionar"))


@bp.route("/habitos/<int:habito_id>/eliminar", methods=["POST"])
def eliminar_habito(habito_id):
    if datos.delete_habito(db.get_db(), db.usuario_actual(), habito_id):
        flash("Hábito eliminado, con todo su historial.", "success")
    else:
        flash("Ese hábito ya no existe.", "error")
    return redirect(url_for("rutina.habitos_gestionar"))


# --- Tareas ------------------------------------------------------------------

def _parse_tarea(form) -> tuple:
    titulo = (form.get("titulo") or "").strip()
    if not titulo:
        raise ValueError("Escribí qué hay que hacer.")
    if len(titulo) > MAX_TITULO:
        raise ValueError(f"El título puede tener hasta {MAX_TITULO} caracteres.")
    notas = (form.get("notas") or "").strip()[:MAX_NOTAS]
    fecha_raw = (form.get("fecha") or "").strip()
    fecha = _fecha(fecha_raw, "la fecha") if fecha_raw else None
    prioridad = _entero(form.get("prioridad") or 1, "la prioridad")
    if prioridad not in PRIORIDADES:
        raise ValueError("Prioridad inválida.")
    return titulo, notas, fecha, prioridad


def _datos_edicion_tarea(t) -> str:
    return json.dumps({
        "titulo": t.titulo,
        "url_editar": url_for("rutina.editar_tarea", tarea_id=t.id),
        "url_eliminar": url_for("rutina.eliminar_tarea", tarea_id=t.id),
        "campos": {
            "titulo": t.titulo, "notas": t.notas,
            "fecha": t.fecha.isoformat() if t.fecha else "", "prioridad": t.prioridad,
        },
    }, ensure_ascii=False)


@bp.route("/tareas")
def tareas():
    conn = db.get_db()
    hoy = date.today()
    todas = datos.get_tareas(conn, db.usuario_actual())
    grupos = clasificar_tareas(todas, hoy)
    lunes = hoy - timedelta(days=hoy.weekday())
    inicio_semana = datetime.combine(lunes, datetime.min.time())
    return render_template(
        "rutina/tareas.html",
        hoy=hoy,
        grupos=grupos,
        edicion={t.id: _datos_edicion_tarea(t) for t in todas},
        volver=url_for("rutina.tareas"),
        para_hoy=sum(1 for t in grupos["hoy"] if t.fecha == hoy),
        atrasadas=sum(1 for t in grupos["hoy"] if t.atrasada(hoy)),
        semana=sum(1 for t in grupos["proximas"] if t.fecha <= hoy + timedelta(days=7)),
        hechas_semana=sum(1 for t in todas if t.hecha and t.hecha >= inicio_semana),
        prioridades=PRIORIDADES,
        fecha_corta=_fecha_corta,
    )


@bp.route("/tareas", methods=["POST"])
def crear_tarea():
    try:
        valores = _parse_tarea(request.form)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        datos.add_tarea(db.get_db(), db.usuario_actual(), *valores)
        flash("Tarea agregada.", "success")
    return redirect(url_for("rutina.tareas"))


@bp.route("/tareas/<int:tarea_id>/editar", methods=["POST"])
def editar_tarea(tarea_id):
    try:
        valores = _parse_tarea(request.form)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        if datos.update_tarea(db.get_db(), db.usuario_actual(), tarea_id, *valores):
            flash("Tarea actualizada.", "success")
        else:
            flash("Esa tarea ya no existe.", "error")
    return redirect(url_for("rutina.tareas"))


@bp.route("/tareas/<int:tarea_id>/completar", methods=["POST"])
def completar_tarea(tarea_id):
    """Marca la tarea como hecha, o la vuelve a pendiente si ya lo estaba."""
    conn = db.get_db()
    volver = ruta_interna(request.form.get("volver"), url_for("rutina.tareas"))
    tarea = datos.get_tarea(conn, db.usuario_actual(), tarea_id)
    if tarea is None:
        flash("Esa tarea ya no existe.", "error")
    else:
        datos.set_tarea_hecha(conn, db.usuario_actual(), tarea_id, tarea.pendiente)
    return redirect(volver)


@bp.route("/tareas/<int:tarea_id>/eliminar", methods=["POST"])
def eliminar_tarea(tarea_id):
    if datos.delete_tarea(db.get_db(), db.usuario_actual(), tarea_id):
        flash("Tarea eliminada.", "success")
    else:
        flash("Esa tarea ya no existe.", "error")
    return redirect(url_for("rutina.tareas"))


# --- Registro diario: sueño, ánimo y peso -------------------------------------

PESO_MIN, PESO_MAX = 20, 400
MAX_NOTA = 500


def _parse_registro(form) -> dict:
    """Solo los campos que vinieron en el formulario; vacío = borrar ese dato."""
    campos = {}
    if "acostarse" in form or "levantarse" in form:
        acostarse = (form.get("acostarse") or "").strip()
        levantarse = (form.get("levantarse") or "").strip()
        if acostarse and levantarse:
            acostarse = _hora(acostarse, "a qué hora te acostaste")
            levantarse = _hora(levantarse, "a qué hora te levantaste")
            horas = horas_de_sueno(acostarse, levantarse)
            if not 1 <= horas <= 16:
                raise ValueError(f"Revisá las horas de sueño: da {horas:.1f} h (tiene que estar entre 1 y 16).".replace(".", ",", 1))
        elif acostarse or levantarse:
            raise ValueError("Para el sueño completá a qué hora te acostaste y a qué hora te levantaste.")
        campos["acostarse"] = acostarse or None
        campos["levantarse"] = levantarse or None
    for campo, nombre in (("sueno_calidad", "la calidad del sueño"), ("animo", "el ánimo")):
        if campo in form:
            valor = (form.get(campo) or "").strip()
            if valor:
                valor = _entero(valor, nombre)
                if not 1 <= valor <= 5:
                    raise ValueError(f"Revisá {nombre}: va de 1 a 5.")
            campos[campo] = valor or None
    if "peso" in form:
        valor = (form.get("peso") or "").strip().replace(",", ".")
        if valor:
            valor = _numero(valor, "el peso")
            if not PESO_MIN <= valor <= PESO_MAX:
                raise ValueError(f"Revisá el peso: tiene que estar entre {PESO_MIN} y {PESO_MAX} kg.")
            valor = round(valor, 1)
        campos["peso"] = valor or None
    if "nota" in form:
        campos["nota"] = (form.get("nota") or "").strip()[:MAX_NOTA]
    return campos


def _fmt(valor: float | None, decimales: int = 1, sufijo: str = "") -> str:
    if valor is None:
        return "—"
    return f"{valor:.{decimales}f}".replace(".", ",") + sufijo


@bp.route("/registro")
def registro():
    conn = db.get_db()
    usuario = db.usuario_actual()
    hoy = date.today()
    try:
        fecha = min(date.fromisoformat(request.args.get("fecha", "")), hoy)
    except ValueError:
        fecha = hoy

    objetivos = datos.get_objetivos(conn, usuario)
    registros = datos.get_registros(conn, usuario, hoy - timedelta(days=89), hoy)
    ultimos7 = [r for d, r in registros.items() if d > hoy - timedelta(days=7)]

    # Series para los gráficos: 30 días de sueño y ánimo, 90 de peso.
    dias30 = [hoy - timedelta(days=i) for i in range(29, -1, -1)]
    dias90 = [hoy - timedelta(days=i) for i in range(89, -1, -1)]
    pesos = {d: r.peso for d, r in registros.items() if r.peso is not None}
    graficos = {
        "dias30": [d.strftime("%d/%m") for d in dias30],
        "sueno": [round(registros[d].horas_sueno, 2) if d in registros and registros[d].horas_sueno is not None else None for d in dias30],
        "animo": [registros[d].animo if d in registros else None for d in dias30],
        "dias90": [d.strftime("%d/%m") for d in dias90],
        "peso": [pesos.get(d) for d in dias90],
        "peso_media": [round(v, 2) if v is not None else None for v in media_movil(pesos, dias90)],
        "sueno_meta": objetivos["sueno_horas"],
        "peso_objetivo": objetivos["peso_objetivo"],
        "animos": {k: v[0] for k, v in ANIMOS.items()},
    }

    sueno7 = promedio(r.horas_sueno for r in ultimos7)
    animo7 = promedio(r.animo for r in ultimos7)
    peso_actual = valor_hace(pesos, hoy, tolerancia=89)
    peso_antes = valor_hace(pesos, hoy - timedelta(days=30))
    return render_template(
        "rutina/registro.html",
        hoy=hoy,
        fecha=fecha,
        nombre_fecha=_fecha_corta(fecha, hoy) if abs((hoy - fecha).days) <= 1 else fecha.strftime("%d/%m/%Y"),
        anterior=(fecha - timedelta(days=1)).isoformat(),
        siguiente=(fecha + timedelta(days=1)).isoformat() if fecha < hoy else None,
        actual=datos.get_registro(conn, usuario, fecha),
        volver=request.full_path.rstrip("?"),
        objetivos=objetivos,
        sueno7=sueno7,
        animo7=animo7,
        emoji_animo7=ANIMOS[round(animo7)][0] if animo7 else "",
        peso_actual=peso_actual,
        variacion_peso=(peso_actual - peso_antes) if peso_actual is not None and peso_antes is not None else None,
        registrados30=sum(1 for d in registros if d > hoy - timedelta(days=30)),
        historial=sorted(registros.values(), key=lambda r: r.fecha, reverse=True),
        graficos=graficos,
        animos=ANIMOS,
        calidades=CALIDADES,
        fmt=_fmt,
        fecha_corta=_fecha_corta,
    )


@bp.route("/registro", methods=["POST"])
def guardar_registro():
    hoy = date.today()
    volver = ruta_interna(request.form.get("volver"), url_for("rutina.registro"))
    try:
        fecha = _fecha(request.form.get("fecha") or hoy.isoformat(), "la fecha")
        if fecha > hoy:
            raise ValueError("No se puede registrar un día que todavía no pasó.")
        campos = _parse_registro(request.form)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(volver)
    datos.guardar_registro(db.get_db(), db.usuario_actual(), fecha, campos)
    flash(f"Registro {'de hoy' if fecha == hoy else 'del ' + fecha.strftime('%d/%m')} guardado.", "success")
    return redirect(volver)


@bp.route("/registro/objetivos", methods=["POST"])
def guardar_objetivos():
    volver = url_for("rutina.registro")
    try:
        sueno = _numero((request.form.get("sueno_horas") or "8").replace(",", "."), "las horas de sueño")
        if not 4 <= sueno <= 12:
            raise ValueError("La meta de sueño tiene que estar entre 4 y 12 horas.")
        peso_raw = (request.form.get("peso_objetivo") or "").strip().replace(",", ".")
        peso = _numero(peso_raw, "el peso objetivo") if peso_raw else None
        if peso is not None and not PESO_MIN <= peso <= PESO_MAX:
            raise ValueError(f"El peso objetivo tiene que estar entre {PESO_MIN} y {PESO_MAX} kg.")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(volver)
    datos.set_objetivos(db.get_db(), db.usuario_actual(), round(sueno, 2), round(peso, 1) if peso else None)
    flash("Objetivos guardados.", "success")
    return redirect(volver)


# --- Aporte a la pantalla Hoy --------------------------------------------------

def aporte_hoy(conn, ahora: datetime):
    from ..hoy import AporteHoy, Indicador, panel

    hoy = ahora.date()
    usuario = db.usuario_actual()
    items = items_checklist(conn, usuario, hoy, hoy)
    hechos = sum(1 for i in items if i["hecho"])
    grupos = clasificar_tareas(datos.get_tareas(conn, usuario), hoy)
    de_hoy = grupos["hoy"]
    atrasadas = sum(1 for t in de_hoy if t.atrasada(hoy))
    volver = url_for("hoy.inicio")

    indicadores = [
        Indicador(
            "Hábitos de hoy",
            f"{hechos}/{len(items)}" if items else "—",
            ("¡Todo hecho!" if hechos == len(items) else f"Te falta{'n' if len(items) - hechos != 1 else ''} {len(items) - hechos}") if items else "Creá tu primer hábito",
            clase="positivo" if items and hechos == len(items) else "",
            url=url_for("rutina.habitos") if items else url_for("rutina.habitos_gestionar"),
        ),
        Indicador(
            "Tareas para hoy",
            str(len(de_hoy)) if de_hoy else "Al día",
            f"{atrasadas} atrasada{'s' if atrasadas != 1 else ''}" if atrasadas else (
                f"{len(grupos['proximas'])} más adelante" if grupos["proximas"] else "Nada pendiente"),
            clase="negativo" if atrasadas else "",
            url=url_for("rutina.tareas"),
        ),
    ]
    # Valor corto (ánimo y sueño); el peso va en la nota para no ocupar dos líneas.
    registro_hoy = datos.get_registro(conn, usuario, hoy)
    partes = []
    if registro_hoy and registro_hoy.emoji:
        partes.append(registro_hoy.emoji)
    if registro_hoy and registro_hoy.horas_sueno is not None:
        partes.append(_fmt(registro_hoy.horas_sueno, 1, " h"))
    if registro_hoy is None:
        nota = "Sueño, ánimo y peso"
    elif registro_hoy.peso is not None:
        nota = _fmt(registro_hoy.peso, 1, " kg")
    else:
        nota = "Registrado"
    indicadores.append(Indicador(
        "Cómo estás",
        " ".join(partes) if partes else ("Registrado" if registro_hoy else "Sin cargar"),
        nota,
        url=url_for("rutina.registro"),
    ))

    return AporteHoy(
        indicadores=indicadores,
        paneles_dia=[
            panel("Hábitos", "rutina/_checklist.html", items=items, fecha=hoy, volver=volver, en_hoy=True),
            panel("Cómo estás", "rutina/_registro_form.html", actual=registro_hoy, fecha=hoy, volver=volver,
                  compacto=True, animos=ANIMOS, calidades=CALIDADES),
            panel("Tareas", "rutina/_tareas_lista.html", tareas=de_hoy, hoy=hoy, volver=volver,
                  editable=False, vacio="No tenés tareas para hoy.", fecha_corta=_fecha_corta),
        ],
    )
