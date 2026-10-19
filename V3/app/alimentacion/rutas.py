"""Pantallas de Alimentación: el diario de comidas (con alimentos, macros a
mano o comidas frecuentes), la lista de alimentos y los objetivos diarios
(calculados o a mano). También arma lo que Alimentación aporta a la
pantalla Hoy."""

import json
from datetime import date, datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import db
from ..routes import _entero, _fecha, _numero, ruta_interna
from ..rutina.rutas import _fecha_corta, _fecha_pedida
from . import datos
from .logica import (
    ACTIVIDADES,
    COMIDAS,
    OBJETIVOS,
    Entrada,
    Macros,
    calcular_objetivos,
    edad,
    gramos_de,
    porcentaje,
    sumar,
)

bp = Blueprint("alimentacion", __name__, url_prefix="/alimentacion")

MAX_NOMBRE = 60
DIAS_GRAFICO = 14
KCAL_MAX = 10000
MACRO_MAX = 1000
CANTIDAD_MAX = 5000
# Nombre, clave en Macros y unidad, en el orden en que se muestran.
MACROS = (("Calorías", "kcal", "kcal"), ("Proteínas", "proteinas", "g"),
          ("Carbohidratos", "carbohidratos", "g"), ("Grasas", "grasas", "g"))


def _num(valor: float | None, decimales: int = 0) -> str:
    if valor is None:
        return "—"
    texto = f"{valor:,.{decimales}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    # Sin ceros sobrantes: 7,50 → 7,5 y 60,0 → 60.
    return texto.rstrip("0").rstrip(",") if "," in texto else texto


def _resumen_macros(m: Macros) -> str:
    return f"P {_num(m.proteinas)} · C {_num(m.carbohidratos)} · G {_num(m.grasas)}"


def _detalle(e: Entrada) -> str:
    if e.manual:
        return "Cargado a mano"
    if e.unidad == "g":
        return f"{_num(e.cantidad, 1)} g"
    return f"{_num(e.cantidad, 2)} × {e.unidad} ({_num(e.gramos)} g)"


def _metas(perfil: dict) -> Macros | None:
    if not perfil["kcal"]:
        return None
    return Macros(perfil["kcal"], perfil["proteinas"] or 0, perfil["carbohidratos"] or 0, perfil["grasas"] or 0)


def _avance(total: Macros, metas: Macros | None) -> list[dict]:
    """Por macro: lo comido, la meta, el % y cuánto falta (o sobra)."""
    filas = []
    for nombre, clave, unidad in MACROS:
        valor = getattr(total, clave)
        meta = getattr(metas, clave) if metas else None
        filas.append({
            "nombre": nombre, "clave": clave, "unidad": unidad, "valor": valor, "meta": meta or None,
            "pct": porcentaje(valor, meta), "resta": (meta - valor) if meta else None,
        })
    return filas


def _comida_de_la_hora(ahora: datetime) -> str:
    if ahora.hour < 11:
        return "desayuno"
    if ahora.hour < 15:
        return "almuerzo"
    if ahora.hour < 19:
        return "merienda"
    return "cena"


# --- Diario --------------------------------------------------------------------

def _datos_edicion_entrada(e: Entrada) -> str:
    campos = {"modo": "manual" if e.manual else "alimento", "comida": e.comida, "fecha": e.fecha.isoformat()}
    if e.manual:
        campos |= {"nombre": e.nombre, "kcal": e.macros.kcal, "proteinas": e.macros.proteinas,
                   "carbohidratos": e.macros.carbohidratos, "grasas": e.macros.grasas}
    else:
        campos |= {"alimento_id": e.alimento_id or "", "cantidad": e.cantidad, "unidad": "g" if e.unidad == "g" else "u"}
    return json.dumps({
        "titulo": e.nombre,
        "url_editar": url_for("alimentacion.editar_entrada", entrada_id=e.id),
        "url_eliminar": url_for("alimentacion.eliminar_entrada", entrada_id=e.id),
        "campos": campos,
    }, ensure_ascii=False)


@bp.route("/")
def diario():
    conn = db.get_db()
    usuario = db.usuario_actual()
    ahora = datetime.now()
    hoy = ahora.date()
    fecha = _fecha_pedida(hoy)
    perfil = datos.get_perfil(conn, usuario)
    metas = _metas(perfil)

    desde = min(fecha, hoy - timedelta(days=DIAS_GRAFICO - 1))
    todas = datos.get_entradas(conn, usuario, desde, hoy)
    del_dia = [e for e in todas if e.fecha == fecha]
    total = sumar(e.macros for e in del_dia)
    grupos = [
        {"clave": clave, "nombre": nombre, "entradas": lista, "total": sumar(e.macros for e in lista)}
        for clave, nombre in COMIDAS.items()
        if (lista := [e for e in del_dia if e.comida == clave])
    ]

    # Últimos días: calorías y proteínas por día, y promedio de los días cargados.
    dias = [hoy - timedelta(days=i) for i in range(DIAS_GRAFICO - 1, -1, -1)]
    por_dia = {d: sumar(e.macros for e in todas if e.fecha == d) for d in dias}
    ultimos7 = [m for d, m in por_dia.items() if d > hoy - timedelta(days=7) and m.kcal]
    promedio7 = sumar(ultimos7).escalar(1 / len(ultimos7)) if ultimos7 else None

    alimentos = datos.get_alimentos(conn, usuario)
    return render_template(
        "alimentacion/diario.html",
        hoy=hoy,
        fecha=fecha,
        nombre_fecha=_fecha_corta(fecha, hoy) if abs((hoy - fecha).days) <= 1 else fecha.strftime("%d/%m/%Y"),
        anterior=(fecha - timedelta(days=1)).isoformat(),
        siguiente=(fecha + timedelta(days=1)).isoformat() if fecha < hoy else None,
        avance=_avance(total, metas),
        metas=metas,
        grupos=grupos,
        edicion={e.id: _datos_edicion_entrada(e) for e in del_dia},
        alimentos=alimentos,
        frecuentes=datos.get_frecuentes(conn, usuario),
        comidas=COMIDAS,
        comida_sugerida=_comida_de_la_hora(ahora) if fecha == hoy else "almuerzo",
        promedio7=promedio7,
        dias_cargados7=len(ultimos7),
        graficos={
            "dias": [d.strftime("%d/%m") for d in dias],
            "kcal": [round(por_dia[d].kcal) for d in dias],
            "proteinas": [round(por_dia[d].proteinas) for d in dias],
            "meta_kcal": metas.kcal if metas else None,
            "meta_proteinas": metas.proteinas if metas else None,
        },
        num=_num,
        resumen_macros=_resumen_macros,
        detalle=_detalle,
        volver=request.full_path.rstrip("?"),
    )


def _parse_macros(form) -> Macros:
    """Macros cargadas a mano; sin calorías, las calcula (4/4/9 kcal por g)."""
    valores = {}
    for nombre, clave, _ in MACROS:
        texto = (form.get(clave) or "").strip().replace(",", ".")
        valores[clave] = _numero(texto, f"las {nombre.lower()}") if texto else None
    for nombre, clave, _ in MACROS[1:]:
        if valores[clave] is not None and not 0 <= valores[clave] <= MACRO_MAX:
            raise ValueError(f"Las {nombre.lower()} tienen que estar entre 0 y {MACRO_MAX} g.")
    p, c, g = (valores[k] or 0 for k in ("proteinas", "carbohidratos", "grasas"))
    kcal = valores["kcal"] if valores["kcal"] is not None else p * 4 + c * 4 + g * 9
    if not 0 <= kcal <= KCAL_MAX:
        raise ValueError(f"Las calorías tienen que estar entre 0 y {KCAL_MAX}.")
    if kcal == 0 and not (p or c or g):
        raise ValueError("Cargá al menos las calorías o alguna de las macros.")
    return Macros(kcal, p, c, g).redondear()


def _parse_comida(form) -> tuple[date, str]:
    fecha = _fecha(form.get("fecha") or date.today().isoformat(), "la fecha")
    if fecha > date.today():
        raise ValueError("No se puede anotar un día que todavía no pasó.")
    comida = form.get("comida") or ""
    if comida not in COMIDAS:
        raise ValueError("Elegí en qué comida va.")
    return fecha, comida


def _entrada_del_form(conn, usuario: int, form) -> Entrada:
    fecha, comida = _parse_comida(form)
    if form.get("modo") == "manual":
        nombre = (form.get("nombre") or "").strip()
        if not nombre:
            raise ValueError("Poné qué comiste (ej. \"Pizza afuera\").")
        if len(nombre) > MAX_NOMBRE:
            raise ValueError(f"El nombre puede tener hasta {MAX_NOMBRE} caracteres.")
        return Entrada(0, fecha, comida, nombre, _parse_macros(form))

    alimento_raw = (form.get("alimento_id") or "").strip()
    if not alimento_raw:
        raise ValueError("Elegí el alimento.")
    alimento = datos.get_alimento(conn, usuario, _entero(alimento_raw, "el alimento"))
    if alimento is None:
        raise ValueError("Ese alimento ya no existe.")
    cantidad = _numero((form.get("cantidad") or "").strip().replace(",", "."), "la cantidad")
    if not 0 < cantidad <= CANTIDAD_MAX:
        raise ValueError("Revisá la cantidad: tiene que ser mayor a 0.")
    por_unidad = form.get("unidad") == "u"
    gramos = gramos_de(alimento, cantidad, por_unidad)
    if gramos > CANTIDAD_MAX:
        raise ValueError(f"Es demasiado: hasta {_num(CANTIDAD_MAX)} g por anotación.")
    return Entrada(0, fecha, comida, alimento.nombre, alimento.macros(gramos), alimento.id, round(cantidad, 2),
                   alimento.unidad if por_unidad else "g", round(gramos, 1))


def _cargar_frecuente(conn, usuario: int, form) -> str:
    fecha, comida = _parse_comida(form)
    frecuente_raw = (form.get("frecuente_id") or "").strip()
    frecuente = None
    if frecuente_raw.isdigit():
        frecuente = next((f for f in datos.get_frecuentes(conn, usuario) if f.id == int(frecuente_raw)), None)
    if frecuente is None:
        raise ValueError("Elegí una comida frecuente.")
    alimentos = {a.id: a for a in datos.get_alimentos(conn, usuario)}
    for item in frecuente.items:
        macros = item.macros
        # Con el alimento todavía en la lista, usa sus valores actuales.
        if item.alimento_id in alimentos and item.gramos:
            macros = alimentos[item.alimento_id].macros(item.gramos)
        datos.guardar_entrada(conn, usuario, None, Entrada(0, fecha, comida, item.nombre, macros, item.alimento_id,
                                                           item.cantidad, item.unidad, item.gramos))
    return f"\"{frecuente.nombre}\" anotada en {COMIDAS[comida].lower()}: {_num(frecuente.macros.kcal)} kcal."


def _volver_al_dia(form) -> str:
    try:
        fecha = _fecha(form.get("fecha"), "la fecha")
    except ValueError:
        return url_for("alimentacion.diario")
    return url_for("alimentacion.diario", fecha=fecha.isoformat()) if fecha != date.today() else url_for("alimentacion.diario")


def _guardar_entrada(entrada_id: int | None):
    conn = db.get_db()
    usuario = db.usuario_actual()
    volver = ruta_interna(request.form.get("volver"), _volver_al_dia(request.form))
    try:
        if entrada_id is None and request.form.get("modo") == "frecuente":
            flash(_cargar_frecuente(conn, usuario, request.form), "success")
            return redirect(_volver_al_dia(request.form))
        entrada = _entrada_del_form(conn, usuario, request.form)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(volver)
    if datos.guardar_entrada(conn, usuario, entrada_id, entrada):
        flash(f"{entrada.nombre}: {_num(entrada.macros.kcal)} kcal {'anotadas' if entrada_id is None else 'actualizadas'}.", "success")
    else:
        flash("Eso ya no está anotado.", "error")
    return redirect(_volver_al_dia(request.form))


@bp.route("/entradas", methods=["POST"])
def crear_entrada():
    return _guardar_entrada(None)


@bp.route("/entradas/<int:entrada_id>/editar", methods=["POST"])
def editar_entrada(entrada_id):
    return _guardar_entrada(entrada_id)


@bp.route("/entradas/<int:entrada_id>/eliminar", methods=["POST"])
def eliminar_entrada(entrada_id):
    if datos.delete_entrada(db.get_db(), db.usuario_actual(), entrada_id):
        flash("Borrado del día.", "success")
    else:
        flash("Eso ya no está anotado.", "error")
    return redirect(_volver_al_dia(request.form))


@bp.route("/frecuentes", methods=["POST"])
def crear_frecuente():
    """Guarda lo anotado en una comida de un día para repetirlo con un clic."""
    conn = db.get_db()
    usuario = db.usuario_actual()
    volver = _volver_al_dia(request.form)
    try:
        fecha, comida = _parse_comida(request.form)
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            raise ValueError("Poné un nombre para la comida frecuente (ej. \"Desayuno de siempre\").")
        if len(nombre) > MAX_NOMBRE:
            raise ValueError(f"El nombre puede tener hasta {MAX_NOMBRE} caracteres.")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(volver)
    entradas = [e for e in datos.get_entradas(conn, usuario, fecha, fecha) if e.comida == comida]
    if not entradas:
        flash("Esa comida no tiene nada anotado.", "error")
        return redirect(volver)
    datos.crear_frecuente(conn, usuario, nombre, comida, entradas)
    flash(f"Guardada \"{nombre}\": la vas a encontrar en \"Comida frecuente\" al anotar.", "success")
    return redirect(volver)


@bp.route("/frecuentes/<int:frecuente_id>/eliminar", methods=["POST"])
def eliminar_frecuente(frecuente_id):
    if datos.delete_frecuente(db.get_db(), db.usuario_actual(), frecuente_id):
        flash("Comida frecuente eliminada.", "success")
    else:
        flash("Esa comida frecuente ya no existe.", "error")
    return redirect(url_for("alimentacion.alimentos") + "#frecuentes")


# --- Alimentos ---------------------------------------------------------------------

def _datos_edicion_alimento(a) -> str:
    return json.dumps({
        "titulo": a.nombre,
        "url_editar": url_for("alimentacion.editar_alimento", alimento_id=a.id),
        "url_eliminar": url_for("alimentacion.eliminar_alimento", alimento_id=a.id),
        "campos": {
            "nombre": a.nombre, "kcal": a.por_100g.kcal, "proteinas": a.por_100g.proteinas,
            "carbohidratos": a.por_100g.carbohidratos, "grasas": a.por_100g.grasas,
            "gramos_unidad": a.gramos_unidad or "", "unidad": a.unidad,
        },
    }, ensure_ascii=False)


@bp.route("/alimentos")
def alimentos():
    conn = db.get_db()
    usuario = db.usuario_actual()
    lista = datos.get_alimentos(conn, usuario)
    return render_template(
        "alimentacion/alimentos.html",
        alimentos=lista,
        edicion={a.id: _datos_edicion_alimento(a) for a in lista},
        frecuentes=datos.get_frecuentes(conn, usuario),
        comidas=COMIDAS,
        num=_num,
        resumen_macros=_resumen_macros,
        detalle=_detalle,
    )


def _parse_alimento(form) -> tuple:
    nombre = (form.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("Poné el nombre del alimento.")
    if len(nombre) > MAX_NOMBRE:
        raise ValueError(f"El nombre puede tener hasta {MAX_NOMBRE} caracteres.")
    por_100g = _parse_macros(form)
    if por_100g.kcal > 900 or por_100g.proteinas + por_100g.carbohidratos + por_100g.grasas > 100:
        raise ValueError("Revisá los valores: van cada 100 g (100 g de algo no pueden tener más de 100 g de macros ni más de 900 kcal).")
    gramos_raw = (form.get("gramos_unidad") or "").strip().replace(",", ".")
    unidad = (form.get("unidad") or "").strip().lower()[:20]
    gramos_unidad = _numero(gramos_raw, "los gramos por unidad") if gramos_raw else None
    if gramos_unidad is not None and not 0 < gramos_unidad <= CANTIDAD_MAX:
        raise ValueError("Los gramos por unidad tienen que ser mayores a 0.")
    if bool(gramos_unidad) != bool(unidad):
        raise ValueError("Para anotar por unidad, completá las dos cosas: cómo se llama la unidad y cuántos gramos pesa.")
    if unidad == "g":
        raise ValueError("La unidad no puede llamarse \"g\": usá algo como \"huevo\", \"vaso\" o \"porción\".")
    return nombre, por_100g, round(gramos_unidad, 1) if gramos_unidad else None, unidad


def _guardar_alimento(alimento_id: int | None):
    try:
        valores = _parse_alimento(request.form)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        if datos.guardar_alimento(db.get_db(), db.usuario_actual(), alimento_id, *valores):
            flash(f"\"{valores[0]}\" {'agregado' if alimento_id is None else 'actualizado'}.", "success")
        else:
            flash("Ese alimento ya no existe.", "error")
    return redirect(url_for("alimentacion.alimentos"))


@bp.route("/alimentos", methods=["POST"])
def crear_alimento():
    return _guardar_alimento(None)


@bp.route("/alimentos/<int:alimento_id>/editar", methods=["POST"])
def editar_alimento(alimento_id):
    return _guardar_alimento(alimento_id)


@bp.route("/alimentos/<int:alimento_id>/eliminar", methods=["POST"])
def eliminar_alimento(alimento_id):
    if datos.delete_alimento(db.get_db(), db.usuario_actual(), alimento_id):
        flash("Alimento eliminado (lo que ya anotaste con él queda igual).", "success")
    else:
        flash("Ese alimento ya no existe.", "error")
    return redirect(url_for("alimentacion.alimentos"))


@bp.route("/alimentos/basicos", methods=["POST"])
def cargar_basicos():
    nuevos = datos.agregar_basicos(db.get_db(), db.usuario_actual())
    if not nuevos:
        flash("Ya tenías todos los alimentos básicos.", "success")
    else:
        flash(f"{nuevos} alimento{'s' if nuevos != 1 else ''} agregado{'s' if nuevos != 1 else ''}.", "success")
    return redirect(url_for("alimentacion.alimentos"))


# --- Objetivos -----------------------------------------------------------------------

@bp.route("/objetivos")
def objetivos():
    conn = db.get_db()
    usuario = db.usuario_actual()
    hoy = date.today()
    perfil = datos.get_perfil(conn, usuario)
    peso_registro = datos.ultimo_peso(conn, usuario)
    calculo = None
    if all(perfil[k] for k in ("sexo", "nacimiento", "altura_cm", "peso", "actividad", "objetivo")):
        calculo = calcular_objetivos(perfil["sexo"], perfil["peso"], perfil["altura_cm"], edad(perfil["nacimiento"], hoy),
                                     perfil["actividad"], perfil["objetivo"])
    return render_template(
        "alimentacion/objetivos.html",
        hoy=hoy,
        perfil=perfil,
        metas=_metas(perfil),
        calculo=calculo,
        edad=edad(perfil["nacimiento"], hoy) if perfil["nacimiento"] else None,
        peso_registro=peso_registro,
        peso_inicial=peso_registro[0] if peso_registro else perfil["peso"],
        actividades=ACTIVIDADES,
        objetivos_tipo=OBJETIVOS,
        num=_num,
    )


@bp.route("/objetivos/calcular", methods=["POST"])
def calcular():
    """Guarda los datos personales y reemplaza los objetivos por los calculados."""
    form = request.form
    hoy = date.today()
    try:
        sexo = form.get("sexo") or ""
        if sexo not in ("h", "m"):
            raise ValueError("Elegí el sexo (la fórmula cambia según el sexo).")
        nacimiento = _fecha(form.get("nacimiento"), "la fecha de nacimiento")
        if not 14 <= edad(nacimiento, hoy) <= 100:
            raise ValueError("Revisá la fecha de nacimiento: la fórmula sirve entre los 14 y los 100 años.")
        altura = _numero((form.get("altura_cm") or "").replace(",", "."), "la altura")
        if not 100 <= altura <= 250:
            raise ValueError("La altura va en centímetros, entre 100 y 250.")
        peso = _numero((form.get("peso") or "").replace(",", "."), "el peso")
        if not 30 <= peso <= 300:
            raise ValueError("El peso tiene que estar entre 30 y 300 kg.")
        actividad, objetivo = form.get("actividad") or "", form.get("objetivo") or ""
        if actividad not in ACTIVIDADES:
            raise ValueError("Elegí tu nivel de actividad.")
        if objetivo not in OBJETIVOS:
            raise ValueError("Elegí tu objetivo.")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("alimentacion.objetivos"))
    calculo = calcular_objetivos(sexo, peso, altura, edad(nacimiento, hoy), actividad, objetivo)
    datos.guardar_perfil(db.get_db(), db.usuario_actual(), {
        "sexo": sexo, "nacimiento": nacimiento, "altura_cm": round(altura, 1), "peso": round(peso, 1),
        "actividad": actividad, "objetivo": objetivo,
        **{k: calculo[k] for k in ("kcal", "proteinas", "carbohidratos", "grasas")},
    })
    flash(f"Objetivos calculados: {_num(calculo['kcal'])} kcal · P {calculo['proteinas']} · C {calculo['carbohidratos']} · "
          f"G {calculo['grasas']} g. Podés ajustarlos a mano.", "success")
    return redirect(url_for("alimentacion.objetivos"))


@bp.route("/objetivos", methods=["POST"])
def guardar_objetivos():
    try:
        metas = _parse_macros(request.form)
        if not 800 <= metas.kcal <= KCAL_MAX:
            raise ValueError(f"Las calorías diarias tienen que estar entre 800 y {KCAL_MAX}.")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("alimentacion.objetivos"))
    datos.guardar_perfil(db.get_db(), db.usuario_actual(), {
        "kcal": round(metas.kcal), "proteinas": round(metas.proteinas),
        "carbohidratos": round(metas.carbohidratos), "grasas": round(metas.grasas),
    })
    flash(f"Objetivos guardados: {_num(metas.kcal)} kcal por día.", "success")
    return redirect(url_for("alimentacion.objetivos"))


# --- Aporte a la pantalla Hoy ----------------------------------------------------------

def aporte_hoy(conn, ahora: datetime):
    from ..hoy import AporteHoy, Indicador, panel

    hoy = ahora.date()
    usuario = db.usuario_actual()
    entradas = datos.get_entradas(conn, usuario, hoy, hoy)
    total = sumar(e.macros for e in entradas)
    metas = _metas(datos.get_perfil(conn, usuario))

    if not entradas:
        valor, nota = "Sin anotar", f"Meta: {_num(metas.kcal)} kcal" if metas else "Anotá lo que comés"
    elif metas:
        resta = metas.kcal - total.kcal
        valor = f"{_num(total.kcal)} kcal"
        nota = f"Te quedan {_num(resta)} kcal" if resta >= 0 else f"{_num(-resta)} kcal arriba de la meta"
    else:
        valor, nota = f"{_num(total.kcal)} kcal", _resumen_macros(total) + " g"
    return AporteHoy(
        indicadores=[Indicador("Comidas", valor, nota, url=url_for("alimentacion.diario"))],
        paneles_dia=[panel("Alimentación", "alimentacion/_hoy.html", avance=_avance(total, metas), metas=metas,
                           comidas=len({e.comida for e in entradas}), num=_num)],
    )
