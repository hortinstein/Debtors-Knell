// Index page: click one or more archetype chips to filter the deck table
// (OR logic -- a row shows if it matches any selected archetype).
(function () {
  "use strict";

  var filterBar = document.getElementById("archetype-filter");
  var table = document.getElementById("deck-table");
  if (!filterBar || !table) return;

  var chips = Array.prototype.slice.call(filterBar.querySelectorAll(".archetype-chip:not(.archetype-clear)"));
  var clearBtn = document.getElementById("archetype-clear");
  var rows = Array.prototype.slice.call(table.tBodies[0].querySelectorAll("tr"));
  var active = new Set();

  function applyFilter() {
    rows.forEach(function (row) {
      if (active.size === 0) {
        row.style.display = "";
        return;
      }
      var rowTags = (row.getAttribute("data-archetypes") || "").split(",").filter(Boolean);
      var match = rowTags.some(function (t) { return active.has(t); });
      row.style.display = match ? "" : "none";
    });
  }

  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var tag = chip.getAttribute("data-archetype");
      if (active.has(tag)) {
        active.delete(tag);
        chip.classList.remove("active");
      } else {
        active.add(tag);
        chip.classList.add("active");
      }
      applyFilter();
    });
  });

  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      active.clear();
      chips.forEach(function (c) { c.classList.remove("active"); });
      applyFilter();
    });
  }
})();
