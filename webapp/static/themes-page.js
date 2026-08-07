// The /themes/ editor: preset cards plus a live per-variable colour editor.
//
// Reads its vocabulary and storage helpers from window.BoabTheme (theme.js),
// so the list of themes and the set of editable properties are declared once.
//
// "The preset's own value" is measured, not hardcoded: to find what --bg is
// under the Ledger theme, the page stamps data-theme="ledger" on a detached
// probe element, reads the computed value, and puts things back. That keeps
// this file honest if style.css changes.
(function () {
  "use strict";

  var T = window.BoabTheme;
  var grid = document.getElementById("var-grid");
  var cards = document.getElementById("theme-cards");
  if (!T || !grid || !cards) return;

  var cssBox = document.getElementById("theme-css");
  var statusEl = document.getElementById("theme-status");
  var root = document.documentElement;

  var tweaks = T.readVars();

  function say(msg) {
    if (!statusEl) return;
    statusEl.textContent = msg;
    if (msg) window.setTimeout(function () { statusEl.textContent = ""; }, 2600);
  }

  // The value a variable has from the stylesheet alone, with the inline
  // tweaks lifted off so they can't be mistaken for the preset's own value.
  function presetValues(themeId) {
    var savedTheme = root.getAttribute("data-theme");
    var savedInline = {};
    T.VARS.forEach(function (name) {
      savedInline[name] = root.style.getPropertyValue(name);
      root.style.removeProperty(name);
    });
    root.setAttribute("data-theme", themeId);

    var computed = window.getComputedStyle(root);
    var out = {};
    T.VARS.forEach(function (name) { out[name] = computed.getPropertyValue(name).trim(); });

    root.setAttribute("data-theme", savedTheme || T.DEFAULT_THEME);
    T.VARS.forEach(function (name) {
      if (savedInline[name]) root.style.setProperty(name, savedInline[name]);
    });
    return out;
  }

  // <input type="color"> only speaks #rrggbb. Named colours and rgb() forms
  // get resolved through a canvas-free round trip; anything still unparseable
  // leaves the picker at its default while the text field keeps the truth.
  function toHex(value) {
    var v = (value || "").trim();
    if (/^#[0-9a-f]{6}$/i.test(v)) return v.toLowerCase();
    if (/^#[0-9a-f]{3}$/i.test(v)) {
      return "#" + v.slice(1).split("").map(function (c) { return c + c; }).join("").toLowerCase();
    }
    var m = v.match(/^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i);
    if (m) {
      return "#" + [m[1], m[2], m[3]].map(function (n) {
        return ("0" + parseInt(n, 10).toString(16)).slice(-2);
      }).join("").toLowerCase();
    }
    return null;
  }

  var rows = {};

  function buildGrid() {
    T.VARS.forEach(function (name) {
      var row = document.createElement("div");
      row.className = "var-row";

      var label = document.createElement("label");
      label.textContent = name;
      label.setAttribute("for", "var" + name);

      var color = document.createElement("input");
      color.type = "color";
      color.id = "var" + name;

      var text = document.createElement("input");
      text.type = "text";
      text.spellcheck = false;

      function change(value) {
        var preset = presetValues(T.current())[name];
        if (!value || value === preset) delete tweaks[name];
        else tweaks[name] = value;
        T.setVars(tweaks);
        refresh();
      }

      color.addEventListener("input", function () { text.value = color.value; change(color.value); });
      text.addEventListener("change", function () { change(text.value.trim()); });

      row.appendChild(label);
      row.appendChild(color);
      row.appendChild(text);
      grid.appendChild(row);
      rows[name] = { row: row, color: color, text: text };
    });
  }

  function buildCards() {
    T.THEMES.forEach(function (theme) {
      var card = document.createElement("button");
      card.type = "button";
      card.className = "theme-card";
      card.setAttribute("data-theme-id", theme.id);

      var h = document.createElement("h3");
      h.textContent = theme.name;
      var p = document.createElement("p");
      p.textContent = theme.blurb;
      var strip = document.createElement("div");
      strip.className = "theme-card-strip";

      var vals = presetValues(theme.id);
      ["--bg", "--card-bg", "--row-alt", "--border", "--accent", "--link", "--header-bg", "--fg"]
        .forEach(function (v) {
          var cell = document.createElement("span");
          cell.style.background = vals[v] || "transparent";
          strip.appendChild(cell);
        });

      card.appendChild(h);
      card.appendChild(p);
      card.appendChild(strip);
      card.addEventListener("click", function () {
        T.setTheme(theme.id);
        refresh();
        say("Switched to " + theme.name + ".");
      });
      cards.appendChild(card);
    });
  }

  function refresh() {
    var active = T.current();
    var preset = presetValues(active);

    Array.prototype.forEach.call(cards.children, function (card) {
      card.classList.toggle("active", card.getAttribute("data-theme-id") === active);
    });

    T.VARS.forEach(function (name) {
      var r = rows[name];
      if (!r) return;
      var effective = Object.prototype.hasOwnProperty.call(tweaks, name) ? tweaks[name] : preset[name];
      var hex = toHex(effective);
      if (hex) r.color.value = hex;
      if (document.activeElement !== r.text) r.text.value = effective || "";
      r.row.classList.toggle("dirty", Object.prototype.hasOwnProperty.call(tweaks, name));
    });

    if (cssBox) {
      var names = Object.keys(tweaks);
      cssBox.value = names.length
        ? ':root[data-theme="' + active + '"] {\n' +
          names.sort().map(function (n) { return "  " + n + ": " + tweaks[n] + ";"; }).join("\n") +
          "\n}\n"
        : "/* No tweaks yet — the " + active + " preset is unmodified. */\n";
    }
  }

  var resetBtn = document.getElementById("theme-reset");
  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      tweaks = {};
      T.setVars(tweaks);
      refresh();
      say("Tweaks cleared.");
    });
  }

  var copyBtn = document.getElementById("theme-copy");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      if (!cssBox) return;
      cssBox.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      if (!ok && navigator.clipboard) {
        navigator.clipboard.writeText(cssBox.value).then(function () { say("Copied."); });
        return;
      }
      say(ok ? "Copied." : "Select the box and copy manually.");
    });
  }

  document.addEventListener("boab:themechange", refresh);

  buildCards();
  buildGrid();
  refresh();
})();
