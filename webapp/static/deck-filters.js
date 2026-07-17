// Index page: click one or more archetype chips and/or mana-color pips to
// filter the deck table. Within a filter group it's OR logic (a row shows if
// it matches *any* selected archetype / *any* selected color); the two
// groups combine with AND (a row must satisfy both active groups at once).
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

  function applyFilter() {
    rows.forEach(function (row) {
      var archOk = true;
      if (activeArchetypes.size > 0) {
        var rowTags = (row.getAttribute("data-archetypes") || "").split(",").filter(Boolean);
        archOk = rowTags.some(function (t) { return activeArchetypes.has(t); });
      }
      var colorOk = true;
      if (activeColors.size > 0) {
        var rowColors = (row.getAttribute("data-colors") || "").split(",").filter(Boolean);
        colorOk = rowColors.some(function (c) { return activeColors.has(c); });
      }
      row.style.display = (archOk && colorOk) ? "" : "none";
    });
  }

  archetypeChips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var tag = chip.getAttribute("data-archetype");
      if (activeArchetypes.has(tag)) {
        activeArchetypes.delete(tag);
        chip.classList.remove("active");
      } else {
        activeArchetypes.add(tag);
        chip.classList.add("active");
      }
      applyFilter();
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
      var color = chip.getAttribute("data-color");
      if (activeColors.has(color)) {
        activeColors.delete(color);
        chip.classList.remove("active");
      } else {
        activeColors.add(color);
        chip.classList.add("active");
      }
      applyFilter();
    });
  });

  if (colorClear) {
    colorClear.addEventListener("click", function () {
      activeColors.clear();
      colorChips.forEach(function (c) { c.classList.remove("active"); });
      applyFilter();
    });
  }
})();
