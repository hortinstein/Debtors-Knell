// Index page: click one or more archetype chips and/or mana-color pips to
// filter the deck table.
//
// Every selection narrows: picking Aggro *and* Combo shows the decks tagged
// both, and picking U *and* G shows the decks that play both colors, not the
// union of the two. (The union is what an OR filter gives you, and it made
// each extra chip return *more* rows than the one before, which reads as the
// filter being broken rather than as a deliberate widening.) The two groups
// combine the same way, so a row must satisfy every active chip at once.
//
// Reruns -- the articles the column reprinted -- are held out of the table
// and the count unless the "show reruns" box is ticked, so the same deck
// doesn't appear twice.
(function () {
  "use strict";

  var table = document.getElementById("deck-table");
  if (!table) return;
  var rows = Array.prototype.slice.call(table.tBodies[0].querySelectorAll("tr"));

  var archetypeBar = document.getElementById("archetype-filter");
  var archetypeChips = archetypeBar
    ? Array.prototype.slice.call(archetypeBar.querySelectorAll(".archetype-chip:not(.archetype-clear)"))
    : [];
  var archetypeClear = document.getElementById("archetype-clear");
  var activeArchetypes = new Set();

  var colorBar = document.getElementById("color-filter");
  var colorChips = colorBar
    ? Array.prototype.slice.call(colorBar.querySelectorAll(".mana-pip-btn"))
    : [];
  var colorClear = document.getElementById("color-clear");
  var activeColors = new Set();

  // The column reprinted 25 of its articles as holiday reruns and year-end
  // retrospectives. They're real, separately published pages, but showing
  // them alongside the originals means the same deck appears in the table
  // twice -- so they're out of the count and out of the table unless asked
  // for. See scripts/build_deck_meta.py.
  var showRerunsToggle = document.getElementById("show-reruns");
  var originalRows = rows.filter(function (r) { return !r.getAttribute("data-rerun"); });

  var statusEl = document.getElementById("filter-status");

  function showingReruns() {
    return !!(showRerunsToggle && showRerunsToggle.checked);
  }

  function rowValues(row, attr) {
    return new Set((row.getAttribute("data-" + attr) || "").split(",").filter(Boolean));
  }

  function hasAll(values, required) {
    var ok = true;
    required.forEach(function (r) { if (!values.has(r)) ok = false; });
    return ok;
  }

  function describe() {
    var parts = [];
    if (activeArchetypes.size) parts.push(Array.from(activeArchetypes).join(" + "));
    if (activeColors.size) {
      parts.push(Array.from(activeColors).join("/") + (activeColors.size > 1 ? " (all)" : ""));
    }
    return parts.join(", ");
  }

  function applyFilter() {
    var withReruns = showingReruns();
    var total = withReruns ? rows.length : originalRows.length;
    var noun = withReruns ? " articles" : " original articles";
    var shown = 0;
    rows.forEach(function (row) {
      var ok = (withReruns || !row.getAttribute("data-rerun")) &&
               hasAll(rowValues(row, "archetypes"), activeArchetypes) &&
               hasAll(rowValues(row, "colors"), activeColors);
      row.style.display = ok ? "" : "none";
      if (ok) shown++;
    });
    if (!statusEl) return;
    if (!activeArchetypes.size && !activeColors.size) {
      statusEl.textContent = "Showing all " + total + noun + ".";
      statusEl.classList.remove("filter-status-empty");
      return;
    }
    statusEl.textContent = "Showing " + shown + " of " + total + noun +
      " matching " + describe() + ".";
    statusEl.classList.toggle("filter-status-empty", shown === 0);
  }

  function toggle(set, chip, value) {
    if (set.has(value)) {
      set.delete(value);
      chip.classList.remove("active");
    } else {
      set.add(value);
      chip.classList.add("active");
    }
    applyFilter();
  }

  archetypeChips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      toggle(activeArchetypes, chip, chip.getAttribute("data-archetype"));
    });
  });

  if (archetypeClear) {
    archetypeClear.addEventListener("click", function () {
      activeArchetypes.clear();
      archetypeChips.forEach(function (c) { c.classList.remove("active"); });
      applyFilter();
    });
  }

  colorChips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      toggle(activeColors, chip, chip.getAttribute("data-color"));
    });
  });

  if (showRerunsToggle) {
    showRerunsToggle.addEventListener("change", applyFilter);
  }

  if (colorClear) {
    colorClear.addEventListener("click", function () {
      activeColors.clear();
      colorChips.forEach(function (c) { c.classList.remove("active"); });
      applyFilter();
    });
  }

  applyFilter();
})();
