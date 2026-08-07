// Site-wide theme switcher.
//
// A theme is just a block of custom-property overrides in style.css keyed on
// html[data-theme]; this file decides which one is active, remembers it, and
// draws the little swatch rail pinned to the right edge.
//
// Two pieces of state live in localStorage:
//   boab-theme      the chosen theme id ("modern" by default)
//   boab-theme-vars {"--bg": "#123456", ...} per-variable tweaks from
//                   /themes/, replayed as inline properties on <html> so they
//                   win over the stylesheet without editing it
//
// base.html applies both inline in <head>, before first paint, so switching
// pages never flashes the previous palette. This file re-applies them (a
// no-op then) and adds the UI.
(function () {
  "use strict";

  var STORE_THEME = "boab-theme";
  var STORE_VARS = "boab-theme-vars";
  var DEFAULT_THEME = "modern";

  var THEMES = [
    {
      id: "modern",
      name: "Modern",
      blurb: "The clean default. Follows your system's light/dark setting.",
      swatch: ["#faf9f6", "#7a3b12", "#2c1c10"],
    },
    {
      id: "ledger",
      name: "Ledger",
      blurb: "An accounts book left in a dry attic — rag paper, iron-gall ink, faded red ruling.",
      swatch: ["#efe4cb", "#7a3212", "#3d2a17"],
    },
    {
      id: "foolscap",
      name: "Foolscap",
      blurb: "The same old-book idea gone pale and cold, like a foxed flyleaf.",
      swatch: ["#f2efe4", "#5a4632", "#33302a"],
    },
    {
      id: "midnight",
      name: "Midnight",
      blurb: "Dark whatever your system says, for reading at 2am.",
      swatch: ["#14110d", "#e0a15c", "#0c0906"],
    },
  ];

  // Every property a theme may set. /themes/ builds its editor from this, and
  // it is the allow-list for replaying saved tweaks -- nothing else gets
  // written onto the document.
  var VARS = [
    "--bg", "--fg", "--muted", "--accent", "--accent-2", "--border",
    "--row-alt", "--link", "--card-bg", "--header-bg", "--header-fg",
    "--gain", "--gain-bg", "--loss", "--loss-bg",
    "--site-sub", "--row-hover", "--desc-fg", "--total-bg", "--chip-ref-bg",
  ];

  function read(key, fallback) {
    try {
      var v = window.localStorage.getItem(key);
      return v === null ? fallback : v;
    } catch (e) {
      return fallback; // private mode, or storage disabled
    }
  }

  function write(key, value) {
    try {
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (e) { /* nothing we can do; the choice just won't persist */ }
  }

  function knownTheme(id) {
    for (var i = 0; i < THEMES.length; i++) if (THEMES[i].id === id) return THEMES[i];
    return null;
  }

  function currentTheme() {
    return knownTheme(read(STORE_THEME, DEFAULT_THEME)) ? read(STORE_THEME, DEFAULT_THEME) : DEFAULT_THEME;
  }

  function readVars() {
    try {
      var parsed = JSON.parse(read(STORE_VARS, "{}"));
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) {
      return {};
    }
  }

  function applyTheme(id) {
    document.documentElement.setAttribute("data-theme", id || DEFAULT_THEME);
  }

  function applyVars(vars) {
    var style = document.documentElement.style;
    VARS.forEach(function (name) { style.removeProperty(name); });
    Object.keys(vars || {}).forEach(function (name) {
      if (VARS.indexOf(name) !== -1 && typeof vars[name] === "string") {
        style.setProperty(name, vars[name]);
      }
    });
  }

  function setTheme(id) {
    applyTheme(id);
    write(STORE_THEME, id);
    document.dispatchEvent(new CustomEvent("boab:themechange", { detail: { theme: id } }));
  }

  function setVars(vars) {
    applyVars(vars);
    write(STORE_VARS, Object.keys(vars || {}).length ? JSON.stringify(vars) : null);
  }

  function buildSwitcher() {
    // The tweak page draws its own, richer picker.
    if (document.getElementById("theme-editor")) return;

    var rail = document.createElement("div");
    rail.className = "theme-switch";
    rail.setAttribute("role", "group");
    rail.setAttribute("aria-label", "Site theme");

    var label = document.createElement("div");
    label.className = "theme-switch-label";
    label.textContent = "Theme";
    rail.appendChild(label);

    var buttons = THEMES.map(function (theme) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "theme-swatch";
      b.setAttribute("data-theme-id", theme.id);
      b.title = theme.name + " — " + theme.blurb;
      b.setAttribute("aria-label", theme.name);
      b.style.background =
        "linear-gradient(135deg, " + theme.swatch[0] + " 0 50%, " +
        theme.swatch[1] + " 50% 78%, " + theme.swatch[2] + " 78% 100%)";
      b.addEventListener("click", function () { setTheme(theme.id); });
      rail.appendChild(b);
      return b;
    });

    var more = document.createElement("a");
    more.className = "theme-switch-more";
    more.href = rail.getAttribute("data-themes-url") || themesUrl();
    more.textContent = "tweak…";
    more.title = "Open the theme editor";
    rail.appendChild(more);

    function markActive() {
      var active = currentTheme();
      buttons.forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-theme-id") === active);
      });
    }
    document.addEventListener("boab:themechange", markActive);
    markActive();

    document.body.appendChild(rail);
  }

  // The site is also served as a frozen static build under a project
  // subpath, so link relative to whatever the nav already points at rather
  // than assuming the app sits at the domain root.
  function themesUrl() {
    var nav = document.querySelector('.site-nav a[href*="themes"]');
    return nav ? nav.getAttribute("href") : "themes/";
  }

  // Exposed for /themes/, which needs the same vocabulary and storage.
  window.BoabTheme = {
    THEMES: THEMES,
    VARS: VARS,
    DEFAULT_THEME: DEFAULT_THEME,
    current: currentTheme,
    setTheme: setTheme,
    readVars: readVars,
    setVars: setVars,
    applyVars: applyVars,
  };

  applyTheme(currentTheme());
  applyVars(readVars());

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildSwitcher);
  } else {
    buildSwitcher();
  }
})();
