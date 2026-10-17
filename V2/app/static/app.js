(function () {
  "use strict";

  function setupThemeToggle() {
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;

    var prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;

    function actual() {
      var explicito = document.documentElement.dataset.theme;
      if (explicito === "light" || explicito === "dark") return explicito;
      return prefersDark ? "dark" : "light";
    }

    btn.addEventListener("click", function () {
      var siguiente = actual() === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = siguiente;
      localStorage.setItem("theme", siguiente);
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

  document.addEventListener("DOMContentLoaded", function () {
    setupThemeToggle();
    setupNavToggle();
    setupFlashes();
    setupConfirmDelete();
  });
})();
