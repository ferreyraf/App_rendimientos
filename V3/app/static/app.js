(function () {
  "use strict";

  function setupThemeToggle() {
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;

    btn.addEventListener("click", function () {
      var siguiente = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = siguiente;
      try {
        sessionStorage.setItem("theme", siguiente);
      } catch (e) {}
    });
  }

  function setupNavToggle() {
    var toggle = document.getElementById("nav-toggle");
    var links = document.getElementById("nav-links");
    if (!toggle || !links) return;

    toggle.addEventListener("click", function () {
      var abierto = links.classList.toggle("nav-links-open");
      toggle.setAttribute("aria-expanded", abierto ? "true" : "false");
    });
  }

  function setupFlashes() {
    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    document.querySelectorAll(".flash").forEach(function (flash) {
      var cerrar = flash.querySelector(".flash-close");
      var descartar = function () {
        flash.remove();
      };
      if (cerrar) cerrar.addEventListener("click", descartar);

      if (!reduceMotion) {
        setTimeout(function () {
          flash.classList.add("flash-fade-out");
          flash.addEventListener("transitionend", descartar, { once: true });
        }, 4000);
      }
    });
  }

  function setupConfirmDelete() {
    // Botones con su propio texto de confirmación (data-confirmar).
    document.addEventListener("click", function (event) {
      var btn = event.target.closest("[data-confirmar]");
      if (btn && !window.confirm(btn.dataset.confirmar)) event.preventDefault();
    });

    document.querySelectorAll('button[formaction*="eliminar"]').forEach(function (btn) {
      btn.addEventListener("click", function (event) {
        if (!window.confirm("¿Confirmás que querés eliminar este registro?")) {
          event.preventDefault();
        }
      });
    });
  }

  // Los números marcados con data-contar suben desde 0 hasta su valor
  // (ej. "−$1.257.048"): se respeta el prefijo, los decimales y el sufijo.
  function setupContadores() {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    document.querySelectorAll("[data-contar]").forEach(function (el) {
      var partes = el.textContent.trim().match(/^([^\d]*)([\d.]+)(,\d+)?(.*)$/);
      if (!partes) return;
      var prefijo = partes[1];
      var final = parseInt(partes[2].replace(/\./g, ""), 10);
      var decimales = partes[3] || "";
      var sufijo = partes[4];
      var duracion = 900;
      var inicio = null;

      function cuadro(ahora) {
        if (inicio === null) inicio = ahora;
        var t = Math.min((ahora - inicio) / duracion, 1);
        var suave = 1 - Math.pow(1 - t, 3);
        el.textContent = prefijo + Math.round(final * suave).toLocaleString("es-AR") + decimales + sufijo;
        if (t < 1) window.requestAnimationFrame(cuadro);
      }
      window.requestAnimationFrame(cuadro);
    });
  }

  // Carruseles: varios paneles (gráficos, listas) que rotan en el mismo
  // lugar en vez de apilarse y obligar a scrollear. El template solo pone
  //   <section class="carrusel" data-carrusel="nombre">
  //     <div class="carrusel-cuerpo">
  //       <div class="carrusel-slide" data-titulo="…">…</div> …
  // y acá se arma la cabecera: pestañas con los títulos (o, con muchos
  // paneles o data-modo="compacto", solo el título actual), flechas y
  // contador. data-modo="ticker" (las alertas) deja solo flechas y rota
  // solo. Un panel con data-oculto="1" se saltea (ej. filtrado). El panel
  // elegido se recuerda mientras la ventana esté abierta.
  var MAX_PESTANAS = 5;
  var ROTACION_TICKER_MS = 8000;

  function setupCarruseles() {
    document.querySelectorAll("[data-carrusel]").forEach(function (carrusel) {
      var cuerpo = carrusel.querySelector(".carrusel-cuerpo");
      if (!cuerpo) return;
      var todos = Array.prototype.slice.call(cuerpo.children).filter(function (el) {
        return el.classList.contains("carrusel-slide");
      });
      if (!todos.length) return;

      var modo = carrusel.dataset.modo || (todos.length > MAX_PESTANAS ? "compacto" : "pestanas");
      var clave = "carrusel:" + carrusel.dataset.carrusel;
      var actual = null;

      var cabecera = document.createElement("div");
      cabecera.className = "carrusel-cabecera";
      var pestanas = document.createElement("div");
      pestanas.className = "carrusel-pestanas";
      pestanas.setAttribute("role", "tablist");
      var titulo = document.createElement("span");
      titulo.className = "carrusel-titulo";
      var controles = document.createElement("div");
      controles.className = "carrusel-controles";
      var anterior = document.createElement("button");
      anterior.type = "button";
      anterior.className = "carrusel-flecha";
      anterior.setAttribute("aria-label", "Anterior");
      anterior.textContent = "‹";
      var contador = document.createElement("span");
      contador.className = "carrusel-contador";
      var siguiente = document.createElement("button");
      siguiente.type = "button";
      siguiente.className = "carrusel-flecha";
      siguiente.setAttribute("aria-label", "Siguiente");
      siguiente.textContent = "›";
      controles.append(anterior, contador, siguiente);

      if (modo === "pestanas") cabecera.append(pestanas, titulo);
      else if (modo === "compacto") cabecera.append(titulo);
      cabecera.append(controles);
      titulo.hidden = modo === "pestanas";
      carrusel.classList.add("carrusel-modo-" + modo);
      carrusel.insertBefore(cabecera, cuerpo);

      function visibles() {
        return todos.filter(function (s) { return s.dataset.oculto !== "1"; });
      }

      function armarPestanas() {
        pestanas.textContent = "";
        visibles().forEach(function (slide) {
          var tab = document.createElement("button");
          tab.type = "button";
          tab.setAttribute("role", "tab");
          tab.className = "carrusel-pestana";
          tab.textContent = slide.dataset.titulo || "";
          tab.addEventListener("click", function () { mostrar(slide); });
          tab.addEventListener("keydown", function (e) {
            if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
              e.preventDefault();
              mover(e.key === "ArrowRight" ? 1 : -1);
              var activa = pestanas.querySelector('[aria-selected="true"]');
              if (activa) activa.focus();
            }
          });
          slide._pestana = tab;
          pestanas.append(tab);
        });
      }

      function mostrar(slide, direccion) {
        var lista = visibles();
        if (!lista.length) return;
        if (lista.indexOf(slide) === -1) slide = lista[0];
        var previo = actual;
        actual = slide;
        todos.forEach(function (s) {
          s.hidden = s !== slide;
          s.classList.remove("carrusel-entra-der", "carrusel-entra-izq");
          if (s._pestana) {
            s._pestana.setAttribute("aria-selected", s === slide ? "true" : "false");
            s._pestana.tabIndex = s === slide ? 0 : -1;
          }
        });
        if (previo && previo !== slide) {
          var dir = direccion || (lista.indexOf(slide) > lista.indexOf(previo) ? 1 : -1);
          void slide.offsetWidth;
          slide.classList.add(dir > 0 ? "carrusel-entra-der" : "carrusel-entra-izq");
        }
        var i = lista.indexOf(slide);
        titulo.textContent = slide.dataset.titulo || "";
        contador.textContent = (i + 1) + " / " + lista.length;
        controles.hidden = lista.length < 2;
        try { sessionStorage.setItem(clave, slide.dataset.titulo || String(i)); } catch (e) {}
        // Chart.js no se entera solo del tamaño si el gráfico estuvo oculto.
        if (window.Chart) {
          slide.querySelectorAll("canvas").forEach(function (canvas) {
            var chart = window.Chart.getChart(canvas);
            if (chart) chart.resize();
          });
        }
      }

      function mover(paso) {
        var lista = visibles();
        if (lista.length < 2) return;
        var i = lista.indexOf(actual);
        mostrar(lista[(i + paso + lista.length) % lista.length], paso);
      }

      anterior.addEventListener("click", function () { mover(-1); });
      siguiente.addEventListener("click", function () { mover(1); });

      // Deslizar con el dedo o el touchpad (arrastre horizontal).
      var inicioX = null;
      cuerpo.addEventListener("pointerdown", function (e) {
        if (e.pointerType !== "mouse") inicioX = e.clientX;
      });
      cuerpo.addEventListener("pointerup", function (e) {
        if (inicioX === null) return;
        var dx = e.clientX - inicioX;
        inicioX = null;
        if (Math.abs(dx) > 60) mover(dx < 0 ? 1 : -1);
      });

      // Cuando cambian los paneles visibles (filtros), rearmar.
      carrusel.addEventListener("carrusel:actualizar", function () {
        if (modo === "pestanas") armarPestanas();
        mostrar(actual);
        ajustarPestanas();
      });
      // Otro script puede pedir ir a un panel: el evento lleva su título.
      carrusel.addEventListener("carrusel:ir", function (e) {
        var destino = todos.filter(function (s) { return s.dataset.titulo === e.detail; })[0];
        if (destino) mostrar(destino);
      });

      // Si las pestañas no entran en una línea, se muestra solo el título
      // del panel actual con las flechas (y vuelve a pestañas si hay lugar).
      function ajustarPestanas() {
        if (modo !== "pestanas") return;
        pestanas.hidden = false;
        titulo.hidden = true;
        var tabs = pestanas.children;
        var desborda = tabs.length > 1 && tabs[tabs.length - 1].offsetTop > tabs[0].offsetTop;
        pestanas.hidden = desborda;
        titulo.hidden = !desborda;
      }

      if (modo === "pestanas") armarPestanas();
      carrusel.classList.add("carrusel-listo");
      var guardado = null;
      try { guardado = sessionStorage.getItem(clave); } catch (e) {}
      var inicial = todos.filter(function (s) {
        return guardado !== null && s.dataset.titulo === guardado;
      })[0] || todos.filter(function (s) { return s.hasAttribute("data-inicial"); })[0] || todos[0];
      mostrar(inicial);
      ajustarPestanas();
      if (window.ResizeObserver) new ResizeObserver(ajustarPestanas).observe(cabecera);

      if (modo === "ticker" && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        var pausado = false;
        carrusel.addEventListener("mouseenter", function () { pausado = true; });
        carrusel.addEventListener("mouseleave", function () { pausado = false; });
        carrusel.addEventListener("focusin", function () { pausado = true; });
        carrusel.addEventListener("focusout", function () { pausado = false; });
        setInterval(function () { if (!pausado) mover(1); }, ROTACION_TICKER_MS);
      }
    });
  }

  // Formularios rápidos (tildar un hábito, completar una tarea): se envían
  // sin recargar y se reemplazan solo las zonas marcadas con
  // data-refrescable (por id) con su versión nueva, tomada de la página a la
  // que redirige el servidor. Sin JS funcionan igual, con una recarga.
  function setupFormulariosRapidos() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!form.matches || !form.matches("form[data-rapido]")) return;
      e.preventDefault();
      var datos = new FormData(form);
      if (e.submitter && e.submitter.name) datos.append(e.submitter.name, e.submitter.value);
      var botones = form.querySelectorAll("button");
      botones.forEach(function (b) { b.disabled = true; });
      fetch(form.action, { method: "POST", body: datos })
        .then(function (r) { return r.text(); })
        .then(function (html) {
          var nueva = new DOMParser().parseFromString(html, "text/html");
          document.querySelectorAll("[data-refrescable]").forEach(function (zona) {
            var reemplazo = nueva.getElementById(zona.id);
            if (reemplazo) zona.innerHTML = reemplazo.innerHTML;
          });
          var avisos = nueva.querySelector(".flash-list");
          if (avisos) {
            var viejos = document.querySelector(".flash-list");
            if (viejos) viejos.remove();
            document.body.append(avisos);
            setupFlashes();
          }
          botones.forEach(function (b) { b.disabled = false; });
        })
        .catch(function () { form.submit(); });
    });
  }

  // Panel de edición genérico (lista + formulario al costado): un clic en
  // una fila [data-editar] con data-panel="<id del panel>" carga sus datos
  // ({titulo, url_editar, campos: {name: valor}, ...}) en el formulario.
  // Los botones [data-solo-edicion] aparecen solo al editar; los que tienen
  // data-url="clave" envían a esa URL de los datos, y data-texto="clave"
  // toma su texto de ahí.
  function setupPanelesEdicion() {
    document.querySelectorAll("[data-panel-edicion]").forEach(function (panel) {
      var form = panel.querySelector("form");
      var urlNueva = form.getAttribute("action");
      var modo = panel.querySelector("[data-panel-modo]");
      var titulo = panel.querySelector("[data-panel-titulo]");
      var guardar = panel.querySelector("[data-guardar]");
      var textos = { modo: modo.textContent, titulo: titulo.textContent, guardar: guardar.textContent };
      var filaActual = null;

      function marcar(fila) {
        if (filaActual) filaActual.removeAttribute("aria-current");
        filaActual = fila;
        if (fila) fila.setAttribute("aria-current", "true");
      }

      function poner(nombre, valor) {
        form.querySelectorAll('[name="' + nombre + '"]').forEach(function (campo) {
          if (campo.type === "checkbox" && Array.isArray(valor)) {
            campo.checked = valor.map(String).indexOf(campo.value) !== -1;
          } else if (campo.type === "checkbox") {
            campo.checked = !!valor;
          } else if (campo.type === "radio") {
            campo.checked = String(valor) === campo.value;
          } else {
            campo.value = valor === null || valor === undefined ? "" : valor;
          }
        });
      }

      function nuevo() {
        form.reset();
        form.action = urlNueva;
        modo.textContent = textos.modo;
        titulo.textContent = textos.titulo;
        guardar.textContent = textos.guardar;
        panel.querySelectorAll("[data-solo-edicion]").forEach(function (el) { el.hidden = true; });
        marcar(null);
        form.dispatchEvent(new Event("panel:actualizado"));
      }

      function editar(datos, fila) {
        form.reset();
        form.action = datos.url_editar;
        modo.textContent = "Editando";
        titulo.textContent = datos.titulo;
        guardar.textContent = "Guardar cambios";
        Object.keys(datos.campos || {}).forEach(function (n) { poner(n, datos.campos[n]); });
        panel.querySelectorAll("[data-solo-edicion]").forEach(function (el) {
          el.hidden = false;
          if (el.dataset.url) el.formAction = datos[el.dataset.url];
          if (el.dataset.texto) el.textContent = datos[el.dataset.texto];
        });
        marcar(fila);
        form.dispatchEvent(new Event("panel:actualizado"));
        var primero = form.querySelector("input:not([type=hidden]):not([type=checkbox]), textarea");
        if (primero) primero.focus();
      }

      document.addEventListener("click", function (e) {
        var fila = e.target.closest('[data-editar][data-panel="' + panel.id + '"]');
        if (fila) editar(JSON.parse(fila.dataset.editar), fila);
      });
      panel.querySelectorAll("[data-panel-cerrar]").forEach(function (b) { b.addEventListener("click", nuevo); });
    });
  }

  // Secciones plegables del menú: la de la página actual siempre abierta;
  // las demás recuerdan si se abrieron mientras la ventana esté abierta.
  function setupSeccionesMenu() {
    document.querySelectorAll(".sidebar-seccion").forEach(function (seccion) {
      var clave = "menu:" + seccion.dataset.seccion;
      if (!seccion.hasAttribute("data-activa")) {
        try { if (sessionStorage.getItem(clave) === "1") seccion.open = true; } catch (e) {}
      }
      seccion.addEventListener("toggle", function () {
        try { sessionStorage.setItem(clave, seccion.open ? "1" : "0"); } catch (e) {}
      });
    });
  }

  // Avisa en el menú si hay una versión más nueva publicada. Consulta una vez
  // por sesión; sin conexión no muestra nada.
  var scriptActual = document.currentScript;
  function setupAvisoVersion() {
    var aviso = document.getElementById("aviso-version");
    var url = scriptActual && scriptActual.dataset.apiVersion;
    if (!aviso || !url) return;

    function mostrar(datos) {
      if (!datos || !datos.nueva) return;
      aviso.href = datos.nueva.url;
      aviso.querySelector("[data-version-nueva]").textContent = datos.nueva.version;
      aviso.hidden = false;
    }

    var guardado = null;
    try { guardado = JSON.parse(sessionStorage.getItem("version") || "null"); } catch (e) {}
    if (guardado) return mostrar(guardado);
    fetch(url)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (datos) {
        try { sessionStorage.setItem("version", JSON.stringify(datos)); } catch (e) {}
        mostrar(datos);
      })
      .catch(function () {});
  }

  document.addEventListener("DOMContentLoaded", function () {
    setupAvisoVersion();
    setupThemeToggle();
    setupNavToggle();
    setupSeccionesMenu();
    setupFormulariosRapidos();
    setupPanelesEdicion();
    setupFlashes();
    setupConfirmDelete();
    setupCarruseles();
    setupContadores();
  });
})();
