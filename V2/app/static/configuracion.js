// Configuración: pestañas, filtros por categoría y el panel lateral que
// edita la fila elegida (o agrega una nueva cuando no hay ninguna elegida).
(function () {
  "use strict";

  var fmtPesos = function (v) {
    return "$" + Number(v).toLocaleString("es-AR", { maximumFractionDigits: 0 });
  };
  var fmtPct = function (v) {
    return Number(v).toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };

  // --- Pestañas -----------------------------------------------------------

  var tablist = document.querySelector(".tabs");
  if (!tablist) return;
  var tabs = Array.prototype.slice.call(tablist.querySelectorAll("[data-tab]"));
  var nombres = tabs.map(function (t) { return t.dataset.tab; });

  function activar(nombre, enfocar) {
    if (nombres.indexOf(nombre) === -1) return;
    tabs.forEach(function (tab) {
      var activa = tab.dataset.tab === nombre;
      tab.setAttribute("aria-selected", activa ? "true" : "false");
      tab.tabIndex = activa ? 0 : -1;
      document.getElementById(tab.getAttribute("aria-controls")).hidden = !activa;
      if (activa && enfocar) tab.focus();
    });
    if (location.hash !== "#" + nombre) history.replaceState(null, "", "#" + nombre);
  }

  tabs.forEach(function (tab, i) {
    tab.addEventListener("click", function () { activar(tab.dataset.tab); });
    tab.addEventListener("keydown", function (e) {
      var destino = null;
      if (e.key === "ArrowRight") destino = tabs[(i + 1) % tabs.length];
      if (e.key === "ArrowLeft") destino = tabs[(i - 1 + tabs.length) % tabs.length];
      if (e.key === "Home") destino = tabs[0];
      if (e.key === "End") destino = tabs[tabs.length - 1];
      if (destino) {
        e.preventDefault();
        activar(destino.dataset.tab, true);
      }
    });
  });

  window.addEventListener("hashchange", function () { activar(location.hash.slice(1)); });
  var inicial = location.hash.slice(1);
  activar(nombres.indexOf(inicial) !== -1 ? inicial : tablist.dataset.tabInicial);

  document.querySelectorAll("[data-ir-ajustes]").forEach(function (link) {
    link.addEventListener("click", function (e) {
      e.preventDefault();
      activar("ajustes");
      window.scrollTo(0, 0);
    });
  });

  // --- Filtros por categoría ----------------------------------------------

  document.querySelectorAll(".filtros").forEach(function (grupo) {
    var panel = grupo.closest("[role=tabpanel]");
    grupo.addEventListener("click", function (e) {
      var boton = e.target.closest(".filtro");
      if (!boton) return;
      grupo.querySelectorAll(".filtro").forEach(function (b) {
        b.setAttribute("aria-pressed", b === boton ? "true" : "false");
      });
      var filtro = boton.dataset.filtro;
      panel.querySelectorAll(".fila-config[data-categoria]").forEach(function (fila) {
        fila.hidden = filtro !== "" && fila.dataset.categoria !== filtro;
      });
      // Los meses que quedan sin filas visibles se ocultan enteros.
      panel.querySelectorAll("details.lista-card").forEach(function (seccion) {
        seccion.hidden = !seccion.querySelector(".fila-config:not([hidden])");
      });
    });
  });

  // --- Panel de edición ---------------------------------------------------

  function Panel(aside) {
    this.aside = aside;
    this.form = aside.querySelector("form");
    this.singular = aside.dataset.singular;
    this.cotizacion = parseFloat(aside.dataset.cotizacion || "0");
    var self = this;

    aside.querySelectorAll("[data-panel-cerrar]").forEach(function (b) {
      b.addEventListener("click", function () { self.nuevo(); });
    });
    var recurrente = this.campo("recurrente");
    if (recurrente) recurrente.addEventListener("change", function () { self.actualizarRecurrente(); });
    this.form.querySelectorAll("input[name=moneda], input[name=monto]").forEach(function (el) {
      el.addEventListener("input", function () { self.actualizarEquivalente(); });
    });
    var cuotas = this.form.querySelector("[data-cuotas]");
    if (cuotas) {
      cuotas.addEventListener("input", function () { self.hastaDesdeCuotas(); });
      this.campo("hasta").addEventListener("input", function () { cuotas.value = ""; });
    }
    this.form.querySelector("[data-eliminar]").addEventListener("click", function (e) {
      if (!window.confirm("¿Confirmás que querés eliminar este registro?")) e.preventDefault();
    });
    this.nuevo();
  }

  Panel.prototype.campo = function (nombre) {
    return this.form.elements.namedItem(nombre);
  };

  Panel.prototype.poner = function (nombre, valor) {
    var el = this.campo(nombre);
    if (!el) return;
    if (el instanceof RadioNodeList) {
      Array.prototype.forEach.call(el, function (r) { r.checked = r.value === valor; });
    } else if (el.type === "checkbox") {
      el.checked = Boolean(valor);
    } else {
      el.value = valor === null || valor === undefined ? "" : valor;
    }
  };

  Panel.prototype.textos = function (modo, titulo, guardar, editando) {
    this.aside.querySelector("[data-panel-modo]").textContent = modo;
    this.aside.querySelector("[data-panel-titulo]").textContent = titulo;
    this.form.querySelector("[data-guardar]").textContent = guardar;
    this.form.querySelector("[data-eliminar]").hidden = !editando;
    this.aside.querySelectorAll("[data-panel-cerrar]").forEach(function (b) { b.hidden = !editando; });
  };

  Panel.prototype.marcarFila = function (fila) {
    var panel = this.aside.closest("[role=tabpanel]");
    panel.querySelectorAll(".fila-config[aria-current]").forEach(function (f) { f.removeAttribute("aria-current"); });
    if (fila) fila.setAttribute("aria-current", "true");
  };

  Panel.prototype.nuevo = function () {
    this.form.reset();
    this.form.action = this.aside.dataset.urlAgregar;
    this.form.querySelector("[data-eliminar]").removeAttribute("formaction");
    var s = this.singular;
    this.textos("Nuevo " + s, "Agregar " + s, "Agregar", false);
    this.marcarFila(null);
    this.mostrarAjustes([]);
    this.actualizarRecurrente();
    this.actualizarEquivalente();
  };

  Panel.prototype.editar = function (d, fila) {
    this.form.reset();
    this.form.action = d.url_editar;
    this.form.querySelector("[data-eliminar]").setAttribute("formaction", d.url_eliminar);
    Object.keys(d).forEach(function (clave) { this.poner(clave, d[clave]); }, this);
    this.textos("Editar " + this.singular, d.etiqueta || this.singular[0].toUpperCase() + this.singular.slice(1), "Guardar cambios", true);
    this.marcarFila(fila);
    this.mostrarAjustes(d.ajustes || []);
    this.actualizarRecurrente();
    this.actualizarEquivalente();
    if (window.matchMedia("(max-width: 1100px)").matches) {
      this.aside.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  Panel.prototype.actualizarRecurrente = function () {
    var check = this.campo("recurrente");
    if (!check) return;
    var rec = check.checked;
    this.aside.querySelectorAll("[data-solo-recurrente]").forEach(function (el) { el.hidden = !rec; });
    this.aside.querySelectorAll("[data-solo-puntual]").forEach(function (el) { el.hidden = rec; });
    this.campo("fecha").required = !rec;
    this.campo("dia_mes").required = rec;
  };

  // "N cuotas" completa "hasta" con la fecha de la última: N meses contando
  // desde el primer mes en que corre (el de "desde", o el actual).
  Panel.prototype.hastaDesdeCuotas = function () {
    var n = parseInt(this.form.querySelector("[data-cuotas]").value, 10);
    var dia = parseInt(this.campo("dia_mes").value, 10) || 1;
    if (!n || n < 1) return;
    var desde = this.campo("desde").value ? new Date(this.campo("desde").value + "T00:00") : new Date();
    var primero = new Date(desde.getFullYear(), desde.getMonth(), 1);
    // Si el día de pago de ese mes ya pasó respecto de "desde", arranca el mes siguiente.
    if (desde.getDate() > dia && !this.campo("dia_habil").checked) primero.setMonth(primero.getMonth() + 1);
    var ultimo = new Date(primero.getFullYear(), primero.getMonth() + n, 0);
    var pad = function (v) { return String(v).padStart(2, "0"); };
    this.campo("hasta").value = ultimo.getFullYear() + "-" + pad(ultimo.getMonth() + 1) + "-" + pad(ultimo.getDate());
  };

  Panel.prototype.actualizarEquivalente = function () {
    var ayuda = this.aside.querySelector("[data-equivalente]");
    if (!ayuda) return;
    var moneda = this.campo("moneda");
    var monto = parseFloat(this.campo("monto").value);
    var enDolares = moneda && moneda.value === "USD";
    if (!enDolares) {
      ayuda.hidden = true;
      return;
    }
    ayuda.hidden = false;
    ayuda.textContent = this.cotizacion
      ? (monto ? "≈ " + fmtPesos(monto * this.cotizacion) + " " : "") + "al dólar blue de hoy (" + fmtPesos(this.cotizacion) + ")"
      : "Se convierte con el dólar blue de Córdoba al guardar";
  };

  Panel.prototype.mostrarAjustes = function (ajustes) {
    var caja = this.aside.querySelector("[data-ajustes]");
    if (!caja) return;
    var lista = caja.querySelector("[data-ajustes-lista]");
    lista.textContent = "";
    ajustes.forEach(function (a) {
      var fila = document.createElement("div");
      var mes = document.createElement("span");
      var pct = document.createElement("strong");
      mes.textContent = a.mes;
      pct.textContent = (a.pct >= 0 ? "+" : "") + fmtPct(a.pct) + "%";
      fila.appendChild(mes);
      fila.appendChild(pct);
      lista.appendChild(fila);
    });
    caja.hidden = ajustes.length === 0;
  };

  var paneles = {};
  document.querySelectorAll(".panel-edicion").forEach(function (aside) {
    paneles[aside.id] = new Panel(aside);
  });

  document.querySelectorAll("[data-nuevo]").forEach(function (boton) {
    boton.addEventListener("click", function () {
      var panel = paneles["panel-" + boton.dataset.nuevo];
      panel.nuevo();
      panel.form.querySelector("input:not([type=hidden])").focus();
    });
  });

  document.addEventListener("click", function (e) {
    var fila = e.target.closest(".fila-config");
    if (!fila) return;
    var aside = fila.closest("[role=tabpanel]").querySelector(".panel-edicion");
    var datos = JSON.parse(fila.dataset.mov || fila.dataset.aporte);
    paneles[aside.id].editar(datos, fila);
  });
})();
