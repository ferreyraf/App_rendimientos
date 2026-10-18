"""Pantallas de Entrenamiento: gimnasio (anotar series en vivo, progreso y
récords), rutinas y ejercicios, y cardio. También arma lo que Entrenamiento
aporta a la pantalla Hoy.

Anotar una serie no deja mensaje (se hace muchas veces y la pantalla ya
muestra el cambio); empezar, terminar, crear o borrar sí.
"""

import json
from datetime import date, datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import db
from ..routes import _entero, _fecha, _numero, ruta_interna
from ..rutina.logica import DIAS_CORTOS, Habito
from . import datos
from .logica import (
    EJERCICIOS_COMUNES,
    GRUPOS,
    TIPOS_CARDIO,
    formato_duracion,
    formato_ritmo,
    lunes_de,
    parse_duracion,
    por_semana,
    progreso,
    records,
    records_por_sesion,
    semanas,
    ultimas_series,
)

bp = Blueprint("entrenamiento", __name__, url_prefix="/entrenamiento")

MAX_NOMBRE = 60
PESO_MAX = 1000
REPS_MAX = 1000
SEMANAS_GRAFICO = 12


@bp.app_template_filter("extraer")
def _extraer(clave, diccionario: dict):
    """En plantillas: `ids|map('extraer', nombres)` pasa de ids a nombres."""
    return diccionario.get(clave, "")


def _fecha_corta(fecha: date, hoy: date) -> str:
    if fecha == hoy:
        return "Hoy"
    if fecha == hoy - timedelta(days=1):
        return "Ayer"
    return fecha.strftime("%d/%m")


def _kg(valor: float | None, decimales: int = 1) -> str:
    if valor is None:
        return "—"
    texto = f"{valor:,.{decimales}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    # Sin ceros sobrantes: 7,50 → 7,5 y 60,0 → 60.
    return texto.rstrip("0").rstrip(",") if "," in texto else texto


def _descripcion_dias(dias) -> str:
    return Habito(0, "", tuple(dias), date.min).descripcion_dias if dias else "Cuando quieras"


def _precarga(sesiones, ejercicio_id: int, cantidad: int, reps_objetivo: int, antes_de: int | None = None):
    """Peso y reps para `cantidad` series nuevas: lo de la última vez (serie
    por serie) o, sin historial, 0 kg y las reps objetivo."""
    previas = ultimas_series(sesiones, ejercicio_id, antes_de)
    filas = []
    for i in range(cantidad):
        if previas:
            ref = previas[min(i, len(previas) - 1)]
            filas.append((ejercicio_id, ref.peso, ref.reps))
        else:
            filas.append((ejercicio_id, 0.0, reps_objetivo))
    return filas


# --- Gimnasio ------------------------------------------------------------------

@bp.route("/gimnasio")
def gimnasio():
    conn = db.get_db()
    usuario = db.usuario_actual()
    hoy = date.today()
    ejercicios = {e.id: e for e in datos.get_ejercicios(conn, usuario, incluir_archivados=True)}
    sesiones = datos.get_sesiones(conn, usuario)
    terminadas = [s for s in sesiones if not s.en_curso]
    en_curso = next((s for s in sesiones if s.en_curso), None)
    rutinas = datos.get_rutinas(conn, usuario)

    # Sesión en curso: series agrupadas por ejercicio, con la referencia de
    # la vez pasada y la marca de récord (contra todo lo anterior).
    bloques_sesion = []
    if en_curso:
        previos = records([s for s in sesiones if s.id != en_curso.id])
        rutina = next((r for r in rutinas if r["id"] == en_curso.rutina_id), None)
        objetivos = {it["ejercicio_id"]: it for it in rutina["items"]} if rutina else {}
        for e_id in en_curso.ejercicios():
            series = [s for s in sorted(en_curso.series, key=lambda s: s.orden) if s.ejercicio_id == e_id]
            antes = ultimas_series(sesiones, e_id, en_curso.id)
            mejor = previos.get(e_id, {}).get("rm", 0.0)
            bloques_sesion.append({
                "ejercicio": ejercicios.get(e_id),
                "series": [{"s": s, "record": bool(mejor) and s.hecha and s.rm > mejor + 1e-9} for s in series],
                "antes": " · ".join(f"{_kg(s.peso)} kg × {s.reps}" for s in antes),
                "objetivo": f"{objetivos[e_id]['series']}×{objetivos[e_id]['reps']}" if e_id in objetivos else "",
            })

    lunes = lunes_de(hoy)
    de_la_semana = [s for s in terminadas if s.fecha >= lunes]
    pr_por_sesion = records_por_sesion(sesiones)
    hace30 = hoy - timedelta(days=30)
    lunes_graf = semanas(hoy, SEMANAS_GRAFICO)
    con_historial = sorted({s.ejercicio_id for ses in terminadas for s in ses.series if s.hecha})
    mejores = records(terminadas)

    return render_template(
        "entrenamiento/gimnasio.html",
        hoy=hoy,
        en_curso=en_curso,
        bloques_sesion=bloques_sesion,
        ejercicios=sorted((e for e in ejercicios.values() if e.activo), key=lambda e: (e.grupo, e.nombre)),
        nombres={e.id: e.nombre for e in ejercicios.values()},
        rutinas=[dict(r, toca_hoy=hoy.weekday() in r["dias"], cuando=_descripcion_dias(r["dias"])) for r in rutinas],
        sesiones_semana=len(de_la_semana),
        volumen_semana=sum(s.volumen for s in de_la_semana),
        ultima=terminadas[-1] if terminadas else None,
        records_30=sum(len(pr_por_sesion.get(s.id, [])) for s in terminadas if s.fecha >= hace30),
        historial=[
            {"s": s, "records": [ejercicios[e].nombre for e in pr_por_sesion.get(s.id, []) if e in ejercicios]}
            for s in reversed(terminadas[-60:])
        ],
        tabla_records=sorted(
            ({"ejercicio": ejercicios[e], **m} for e, m in mejores.items() if e in ejercicios),
            key=lambda r: (r["ejercicio"].grupo, r["ejercicio"].nombre),
        ),
        graficos={
            "semanas": [l.strftime("%d/%m") for l in lunes_graf],
            "volumen": por_semana(terminadas, lunes_graf, lambda s: s.fecha, lambda s: s.volumen),
            "progreso": {
                str(e): {
                    "nombre": ejercicios[e].nombre,
                    "puntos": [dict(p, fecha=p["fecha"].strftime("%d/%m/%y")) for p in progreso(terminadas, e)],
                }
                for e in con_historial if e in ejercicios
            },
        },
        kg=_kg,
        fecha_corta=_fecha_corta,
        volver=url_for("entrenamiento.gimnasio"),
    )


@bp.route("/sesiones", methods=["POST"])
def empezar_sesion():
    """Empieza un entrenamiento: de una rutina (con sus series precargadas) o libre."""
    conn = db.get_db()
    usuario = db.usuario_actual()
    destino = url_for("entrenamiento.gimnasio")
    if datos.get_sesion_en_curso(conn, usuario):
        flash("Ya tenés un entrenamiento en curso: terminalo o descartalo antes de empezar otro.", "error")
        return redirect(destino)
    rutina_raw = (request.form.get("rutina_id") or "").strip()
    if rutina_raw:
        try:
            rutina = datos.get_rutina(conn, usuario, _entero(rutina_raw, "la rutina"))
        except ValueError:
            rutina = None
        if rutina is None:
            flash("Esa rutina ya no existe.", "error")
            return redirect(destino)
        sesiones = datos.get_sesiones(conn, usuario)
        series = [fila for it in rutina["items"] for fila in _precarga(sesiones, it["ejercicio_id"], it["series"], it["reps"])]
        datos.crear_sesion(conn, usuario, rutina["nombre"], rutina["id"], series)
        flash(f"¡A entrenar! {rutina['nombre']}: anotá cada serie cuando la termines.", "success")
    else:
        datos.crear_sesion(conn, usuario, "Entrenamiento libre", None, [])
        flash("Entrenamiento libre: agregá los ejercicios a medida que los hagas.", "success")
    return redirect(destino)


@bp.route("/sesiones/<int:sesion_id>/series", methods=["POST"])
def agregar_serie(sesion_id):
    """Otra serie de un ejercicio (copia la última) o un ejercicio nuevo."""
    conn = db.get_db()
    usuario = db.usuario_actual()
    volver = ruta_interna(request.form.get("volver"), url_for("entrenamiento.gimnasio"))
    try:
        ejercicio = datos.get_ejercicio(conn, usuario, _entero(request.form.get("ejercicio_id"), "el ejercicio"))
    except ValueError:
        ejercicio = None
    sesion = datos.get_sesion_en_curso(conn, usuario)
    if ejercicio is None or sesion is None or sesion.id != sesion_id:
        flash("No se pudo agregar la serie.", "error")
        return redirect(volver)
    actuales = [s for s in sorted(sesion.series, key=lambda s: s.orden) if s.ejercicio_id == ejercicio.id]
    if actuales:
        peso, reps = actuales[-1].peso, actuales[-1].reps
    else:
        _, peso, reps = _precarga(datos.get_sesiones(conn, usuario), ejercicio.id, 1, 10, sesion.id)[0]
    datos.agregar_serie(conn, usuario, sesion_id, ejercicio.id, peso, reps)
    return redirect(volver)


@bp.route("/series/<int:serie_id>", methods=["POST"])
def anotar_serie(serie_id):
    """Guarda peso y reps de una serie y la marca hecha; con deshacer=1 la
    vuelve a pendiente (Enter en un campo guarda sin desmarcar)."""
    volver = ruta_interna(request.form.get("volver"), url_for("entrenamiento.gimnasio"))
    hecha = request.form.get("deshacer") != "1"
    try:
        peso = _numero((request.form.get("peso") or "0").replace(",", "."), "el peso")
        reps = _entero(request.form.get("reps") or "0", "las repeticiones")
        if not 0 <= peso <= PESO_MAX:
            raise ValueError(f"El peso tiene que estar entre 0 y {PESO_MAX} kg.")
        if not 0 <= reps <= REPS_MAX:
            raise ValueError(f"Las repeticiones tienen que estar entre 0 y {REPS_MAX}.")
        if hecha and reps == 0:
            raise ValueError("Para marcar la serie como hecha, anotá cuántas repeticiones hiciste.")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(volver)
    if not datos.actualizar_serie(db.get_db(), db.usuario_actual(), serie_id, round(peso, 2), reps, hecha):
        flash("Esa serie ya no existe.", "error")
    return redirect(volver)


@bp.route("/series/<int:serie_id>/eliminar", methods=["POST"])
def eliminar_serie(serie_id):
    volver = ruta_interna(request.form.get("volver"), url_for("entrenamiento.gimnasio"))
    if not datos.eliminar_serie(db.get_db(), db.usuario_actual(), serie_id):
        flash("Esa serie ya no existe.", "error")
    return redirect(volver)


@bp.route("/sesiones/<int:sesion_id>/terminar", methods=["POST"])
def terminar_sesion(sesion_id):
    conn = db.get_db()
    usuario = db.usuario_actual()
    sesion = datos.get_sesion_en_curso(conn, usuario)
    if sesion is None or sesion.id != sesion_id:
        flash("Ese entrenamiento ya no está en curso.", "error")
    elif not sesion.series_hechas:
        flash("No marcaste ninguna serie: si no entrenaste, descartalo.", "error")
    else:
        datos.terminar_sesion(conn, usuario, sesion_id, (request.form.get("notas") or "").strip()[:500])
        flash(f"Entrenamiento terminado: {sesion.series_hechas} series, {_kg(sesion.volumen, 0)} kg levantados. ¡Bien ahí!", "success")
    return redirect(url_for("entrenamiento.gimnasio"))


@bp.route("/sesiones/<int:sesion_id>/eliminar", methods=["POST"])
def eliminar_sesion(sesion_id):
    if datos.delete_sesion(db.get_db(), db.usuario_actual(), sesion_id):
        flash("Entrenamiento eliminado.", "success")
    else:
        flash("Ese entrenamiento ya no existe.", "error")
    return redirect(url_for("entrenamiento.gimnasio"))


# --- Rutinas y ejercicios ----------------------------------------------------------

def _parse_ejercicio(form) -> tuple[str, str]:
    nombre = (form.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("Poné el nombre del ejercicio.")
    if len(nombre) > MAX_NOMBRE:
        raise ValueError(f"El nombre puede tener hasta {MAX_NOMBRE} caracteres.")
    grupo = (form.get("grupo") or "").strip()
    if grupo and grupo not in GRUPOS:
        raise ValueError("Grupo muscular inválido.")
    return nombre, grupo


def _parse_rutina(form, ejercicios_validos: set[int]) -> tuple:
    nombre = (form.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("Poné un nombre para la rutina (ej. \"Día A: pecho y tríceps\").")
    if len(nombre) > MAX_NOMBRE:
        raise ValueError(f"El nombre puede tener hasta {MAX_NOMBRE} caracteres.")
    dias = sorted({_entero(d, "los días") for d in form.getlist("dias")})
    if any(not 0 <= d <= 6 for d in dias):
        raise ValueError("Día de la semana inválido.")
    items = []
    for e, s, r in zip(form.getlist("ejercicio_id"), form.getlist("series"), form.getlist("reps")):
        if not e:
            continue
        e = _entero(e, "el ejercicio")
        if e not in ejercicios_validos:
            raise ValueError("Uno de los ejercicios ya no existe.")
        s, r = _entero(s or 3, "las series"), _entero(r or 10, "las repeticiones")
        if not 1 <= s <= 20 or not 1 <= r <= 100:
            raise ValueError("Las series van de 1 a 20 y las repeticiones de 1 a 100.")
        items.append((e, s, r))
    if not items:
        raise ValueError("Agregá al menos un ejercicio a la rutina.")
    return nombre, dias, items


@bp.route("/rutinas")
def rutinas():
    conn = db.get_db()
    usuario = db.usuario_actual()
    todos = datos.get_ejercicios(conn, usuario, incluir_archivados=True)
    lista = datos.get_rutinas(conn, usuario)
    editando = None
    if request.args.get("editar", "").isdigit():
        editando = next((r for r in lista if r["id"] == int(request.args["editar"])), None)
    return render_template(
        "entrenamiento/rutinas.html",
        rutinas=[dict(r, cuando=_descripcion_dias(r["dias"])) for r in lista],
        editando=editando,
        ejercicios=todos,
        activos=[e for e in todos if e.activo],
        nombres={e.id: e.nombre for e in todos},
        grupos=GRUPOS,
        dias_cortos=DIAS_CORTOS,
        hoy=date.today(),
    )


def _guardar_rutina(rutina_id: int | None):
    conn = db.get_db()
    usuario = db.usuario_actual()
    validos = {e.id for e in datos.get_ejercicios(conn, usuario)}
    try:
        nombre, dias, items = _parse_rutina(request.form, validos)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("entrenamiento.rutinas", editar=rutina_id) if rutina_id else url_for("entrenamiento.rutinas"))
    if datos.guardar_rutina(conn, usuario, rutina_id, nombre, dias, items) is None:
        flash("Esa rutina ya no existe.", "error")
    else:
        flash(f"Rutina \"{nombre}\" {'actualizada' if rutina_id else 'creada'}.", "success")
    return redirect(url_for("entrenamiento.rutinas"))


@bp.route("/rutinas", methods=["POST"])
def crear_rutina():
    return _guardar_rutina(None)


@bp.route("/rutinas/<int:rutina_id>/editar", methods=["POST"])
def editar_rutina(rutina_id):
    return _guardar_rutina(rutina_id)


@bp.route("/rutinas/<int:rutina_id>/eliminar", methods=["POST"])
def eliminar_rutina(rutina_id):
    if datos.delete_rutina(db.get_db(), db.usuario_actual(), rutina_id):
        flash("Rutina eliminada (los entrenamientos hechos quedan en el historial).", "success")
    else:
        flash("Esa rutina ya no existe.", "error")
    return redirect(url_for("entrenamiento.rutinas"))


@bp.route("/ejercicios", methods=["POST"])
def crear_ejercicio():
    try:
        nombre, grupo = _parse_ejercicio(request.form)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        datos.add_ejercicio(db.get_db(), db.usuario_actual(), nombre, grupo)
        flash(f"Ejercicio \"{nombre}\" agregado.", "success")
    return redirect(url_for("entrenamiento.rutinas") + "#ejercicios")


@bp.route("/ejercicios/comunes", methods=["POST"])
def cargar_comunes():
    """Agrega los ejercicios más comunes que todavía no estén (por nombre)."""
    conn = db.get_db()
    usuario = db.usuario_actual()
    existentes = {e.nombre.lower() for e in datos.get_ejercicios(conn, usuario, incluir_archivados=True)}
    nuevos = [(n, g) for n, g in EJERCICIOS_COMUNES if n.lower() not in existentes]
    for nombre, grupo in nuevos:
        datos.add_ejercicio(conn, usuario, nombre, grupo)
    flash(f"{len(nuevos)} ejercicios agregados." if nuevos else "Ya tenías todos los ejercicios comunes.", "success")
    return redirect(url_for("entrenamiento.rutinas"))


@bp.route("/ejercicios/<int:ejercicio_id>/editar", methods=["POST"])
def editar_ejercicio(ejercicio_id):
    try:
        nombre, grupo = _parse_ejercicio(request.form)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        if datos.update_ejercicio(db.get_db(), db.usuario_actual(), ejercicio_id, nombre, grupo):
            flash("Ejercicio actualizado.", "success")
        else:
            flash("Ese ejercicio ya no existe.", "error")
    return redirect(url_for("entrenamiento.rutinas"))


@bp.route("/ejercicios/<int:ejercicio_id>/eliminar", methods=["POST"])
def eliminar_ejercicio(ejercicio_id):
    """Solo se borra si nunca se usó; si no, se archiva (deja de ofrecerse
    pero su historial y su progreso quedan)."""
    conn = db.get_db()
    usuario = db.usuario_actual()
    ejercicio = datos.get_ejercicio(conn, usuario, ejercicio_id)
    if ejercicio is None:
        flash("Ese ejercicio ya no existe.", "error")
    elif datos.ejercicio_en_uso(conn, ejercicio_id):
        datos.set_ejercicio_activo(conn, usuario, ejercicio_id, not ejercicio.activo)
        flash(f"\"{ejercicio.nombre}\" está en tu historial o en una rutina: lo {'archivé' if ejercicio.activo else 'reactivé'} en vez de borrarlo.", "success")
    else:
        datos.delete_ejercicio(conn, usuario, ejercicio_id)
        flash(f"Ejercicio \"{ejercicio.nombre}\" eliminado.", "success")
    return redirect(url_for("entrenamiento.rutinas"))


# --- Cardio ----------------------------------------------------------------------

def _parse_actividad(form) -> tuple:
    fecha = _fecha(form.get("fecha") or date.today().isoformat(), "la fecha")
    if fecha > date.today():
        raise ValueError("No se puede registrar una actividad de un día que todavía no pasó.")
    tipo = form.get("tipo") or ""
    if tipo not in TIPOS_CARDIO:
        raise ValueError("Elegí el tipo de actividad.")
    duracion = parse_duracion(form.get("duracion") or "")
    if not 60 <= duracion <= 24 * 3600:
        raise ValueError("La duración tiene que estar entre 1 minuto y 24 horas.")
    distancia_raw = (form.get("distancia_km") or "").strip().replace(",", ".")
    distancia = _numero(distancia_raw, "la distancia") if distancia_raw else None
    if distancia is not None and not 0 < distancia <= 500:
        raise ValueError("La distancia tiene que estar entre 0 y 500 km.")
    fc_raw = (form.get("fc_media") or "").strip()
    fc = _entero(fc_raw, "las pulsaciones") if fc_raw else None
    if fc is not None and not 30 <= fc <= 250:
        raise ValueError("Las pulsaciones promedio tienen que estar entre 30 y 250.")
    notas = (form.get("notas") or "").strip()[:200]
    return fecha, tipo, duracion, round(distancia, 2) if distancia else None, fc, notas


def _datos_edicion_actividad(a) -> str:
    return json.dumps({
        "titulo": f"{a.nombre_tipo} del {a.fecha.strftime('%d/%m')}",
        "url_editar": url_for("entrenamiento.editar_actividad", actividad_id=a.id),
        "url_eliminar": url_for("entrenamiento.eliminar_actividad", actividad_id=a.id),
        "campos": {
            "tipo": a.tipo, "fecha": a.fecha.isoformat(), "duracion": formato_duracion(a.duracion_s),
            "distancia_km": a.distancia_km if a.distancia_km is not None else "",
            "fc_media": a.fc_media if a.fc_media is not None else "", "notas": a.notas,
        },
    }, ensure_ascii=False)


@bp.route("/cardio")
def cardio():
    conn = db.get_db()
    hoy = date.today()
    actividades = datos.get_actividades(conn, db.usuario_actual())
    lunes = lunes_de(hoy)
    semana = [a for a in actividades if a.fecha >= lunes]
    mes = [a for a in actividades if a.fecha >= hoy.replace(day=1)]
    carreras_30 = [a for a in actividades if a.tipo == "correr" and a.ritmo_s_km and a.fecha > hoy - timedelta(days=30)]
    lunes_graf = semanas(hoy, SEMANAS_GRAFICO)
    carreras = [a for a in actividades if a.tipo == "correr" and a.ritmo_s_km][-30:]
    return render_template(
        "entrenamiento/cardio.html",
        hoy=hoy,
        km_semana=sum(a.distancia_km or 0 for a in semana),
        km_mes=sum(a.distancia_km or 0 for a in mes),
        sesiones_semana=len(semana),
        minutos_semana=sum(a.duracion_s for a in semana) // 60,
        mejor_ritmo=min((a.ritmo_s_km for a in carreras_30), default=None),
        historial=list(reversed(actividades[-100:])),
        edicion={a.id: _datos_edicion_actividad(a) for a in actividades[-100:]},
        tipos=TIPOS_CARDIO,
        graficos={
            "semanas": [l.strftime("%d/%m") for l in lunes_graf],
            "km": [round(v, 2) for v in por_semana(actividades, lunes_graf, lambda a: a.fecha, lambda a: a.distancia_km or 0)],
            "ritmo_fechas": [a.fecha.strftime("%d/%m") for a in carreras],
            "ritmo": [round(a.ritmo_s_km) for a in carreras],
        },
        kg=_kg,
        formato_duracion=formato_duracion,
        formato_ritmo=formato_ritmo,
        fecha_corta=_fecha_corta,
    )


def _guardar_actividad(actividad_id: int | None):
    try:
        valores = _parse_actividad(request.form)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        if datos.guardar_actividad(db.get_db(), db.usuario_actual(), actividad_id, *valores):
            emoji, nombre = TIPOS_CARDIO[valores[1]]
            flash(f"{emoji} {nombre} {'guardado' if actividad_id is None else 'actualizado'}.", "success")
        else:
            flash("Esa actividad ya no existe.", "error")
    return redirect(ruta_interna(request.form.get("volver"), url_for("entrenamiento.cardio")))


@bp.route("/cardio", methods=["POST"])
def crear_actividad():
    return _guardar_actividad(None)


@bp.route("/cardio/<int:actividad_id>/editar", methods=["POST"])
def editar_actividad(actividad_id):
    return _guardar_actividad(actividad_id)


@bp.route("/cardio/<int:actividad_id>/eliminar", methods=["POST"])
def eliminar_actividad(actividad_id):
    if datos.delete_actividad(db.get_db(), db.usuario_actual(), actividad_id):
        flash("Actividad eliminada.", "success")
    else:
        flash("Esa actividad ya no existe.", "error")
    return redirect(url_for("entrenamiento.cardio"))


# --- Aporte a la pantalla Hoy ------------------------------------------------------

def aporte_hoy(conn, ahora: datetime):
    from ..hoy import AporteHoy, Indicador, panel

    hoy = ahora.date()
    usuario = db.usuario_actual()
    sesiones_hoy = datos.get_sesiones(conn, usuario, hoy)
    en_curso = datos.get_sesion_en_curso(conn, usuario)
    hechas = [s for s in sesiones_hoy if not s.en_curso]
    cardio_hoy = [a for a in datos.get_actividades(conn, usuario, hoy) if a.fecha == hoy]
    de_hoy = [r for r in datos.get_rutinas(conn, usuario) if hoy.weekday() in r["dias"]]

    if en_curso:
        valor, nota = "En curso", f"{en_curso.nombre} · {en_curso.series_hechas} series"
    elif hechas or cardio_hoy:
        partes = [s.nombre for s in hechas] + [f"{a.emoji} {_kg(a.distancia_km)} km" if a.distancia_km else a.nombre_tipo for a in cardio_hoy]
        valor, nota = "✓ Hecho", " · ".join(partes)
    elif de_hoy:
        valor, nota = "Toca hoy", " · ".join(r["nombre"] for r in de_hoy)
    else:
        valor, nota = "Descanso", "Nada planificado"
    return AporteHoy(
        indicadores=[Indicador("Entrenamiento", valor, nota, clase="positivo" if valor == "✓ Hecho" else "",
                               url=url_for("entrenamiento.gimnasio"))],
        paneles_dia=[panel("Entrenamiento", "entrenamiento/_hoy.html", en_curso=en_curso, hechas=hechas,
                           cardio=cardio_hoy, rutinas=de_hoy, kg=_kg, formato_duracion=formato_duracion)],
    )
