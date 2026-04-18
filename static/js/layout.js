/* Meili Property — layout behaviour: sidebar state, dropdowns, submenus.
   No framework — vanilla JS, loaded at end of body.
*/
(function () {
  "use strict";

  const LS_SIDEBAR = "meili.sidebar.collapsed";
  const LS_OPEN_SECTIONS = "meili.sidebar.open";

  const shell = document.querySelector(".app-shell");
  if (!shell) return;

  // ---- sidebar collapse state ------------------------------------------
  const savedCollapsed = localStorage.getItem(LS_SIDEBAR) === "1";
  if (savedCollapsed) shell.classList.add("collapsed");

  const toggle = document.getElementById("sidebarToggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      shell.classList.toggle("collapsed");
      localStorage.setItem(LS_SIDEBAR, shell.classList.contains("collapsed") ? "1" : "0");
    });
  }

  // ---- submenu accordion state -----------------------------------------
  const openSet = new Set(JSON.parse(localStorage.getItem(LS_OPEN_SECTIONS) || "[]"));
  document.querySelectorAll(".sidebar-menu li[data-section]").forEach(function (li) {
    const key = li.getAttribute("data-section");
    if (openSet.has(key)) li.classList.add("open");
    const btn = li.querySelector(":scope > .menu-toggle");
    if (btn) {
      btn.addEventListener("click", function () {
        li.classList.toggle("open");
        if (li.classList.contains("open")) openSet.add(key);
        else openSet.delete(key);
        localStorage.setItem(LS_OPEN_SECTIONS, JSON.stringify(Array.from(openSet)));
      });
    }
  });

  // ---- header dropdowns -------------------------------------------------
  document.querySelectorAll("[data-dropdown]").forEach(function (el) {
    const trigger = el.querySelector("[data-dropdown-trigger]");
    if (!trigger) return;
    trigger.addEventListener("click", function (e) {
      e.stopPropagation();
      const wasOpen = el.classList.contains("open");
      document.querySelectorAll("[data-dropdown].open").forEach(function (o) { o.classList.remove("open"); });
      if (!wasOpen) el.classList.add("open");
    });
  });
  document.addEventListener("click", function () {
    document.querySelectorAll("[data-dropdown].open").forEach(function (o) { o.classList.remove("open"); });
  });

  // ---- auto-submit global search ---------------------------------------
  // No JS — submits via form on Enter. Debounced live-search can be added later.
})();
