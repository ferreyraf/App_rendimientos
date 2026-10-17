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
    setupFlashes();
    setupConfirmDelete();
    setupContadores();
  });
})();
