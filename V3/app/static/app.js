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

      if (modo === "pestanas") cabecera.append(pestanas);
      else if (modo === "compacto") cabecera.append(titulo);
      cabecera.append(controles);
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
      });
      // Otro script puede pedir ir a un panel: el evento lleva su título.
      carrusel.addEventListener("carrusel:ir", function (e) {
        var destino = todos.filter(function (s) { return s.dataset.titulo === e.detail; })[0];
        if (destino) mostrar(destino);
      });

      if (modo === "pestanas") armarPestanas();
      carrusel.classList.add("carrusel-listo");
      var guardado = null;
      try { guardado = sessionStorage.getItem(clave); } catch (e) {}
      var inicial = todos.filter(function (s) {
        return guardado !== null && s.dataset.titulo === guardado;
      })[0] || todos.filter(function (s) { return s.hasAttribute("data-inicial"); })[0] || todos[0];
      mostrar(inicial);

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
    setupFlashes();
    setupConfirmDelete();
    setupCarruseles();
    setupContadores();
  });
})();
