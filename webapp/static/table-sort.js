// Vanilla-JS sortable table + click-to-open rows. No external dependencies.
// Works on every table.deck-table on the page (index page, card pool page).
(function () {
  "use strict";

  var tables = document.querySelectorAll("table.deck-table");
  if (!tables.length) return;

  function initTable(table) {
    var tbody = table.tBodies[0];

    // Clicking anywhere on a row with a data-href navigates there (index
    // page); the pool page's rows have no data-href, so this is a no-op
    // there and checkboxes/links behave normally.
    tbody.addEventListener("click", function (evt) {
      if (evt.target.closest("a") || evt.target.closest("input")) return;
      var row = evt.target.closest("tr[data-href]");
      if (row) {
        window.location.href = row.getAttribute("data-href");
      }
    });

    var headers = table.querySelectorAll("th.sortable");
    var currentKey = null;
    var currentDir = 1;

    function rowValue(row, key, type) {
      var v = row.getAttribute("data-" + key) || "";
      if (type === "num") return parseFloat(v) || 0;
      return v;
    }

    function sortBy(key, type, dir) {
      var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
      rows.sort(function (a, b) {
        var va = rowValue(a, key, type);
        var vb = rowValue(b, key, type);
        if (va < vb) return -1 * dir;
        if (va > vb) return 1 * dir;
        return 0;
      });
      rows.forEach(function (r) { tbody.appendChild(r); });
    }

    headers.forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.getAttribute("data-key");
        var type = th.getAttribute("data-type");
        if (currentKey === key) {
          currentDir *= -1;
        } else {
          currentKey = key;
          currentDir = 1;
        }
        headers.forEach(function (h) { h.classList.remove("sort-asc", "sort-desc"); });
        th.classList.add(currentDir === 1 ? "sort-asc" : "sort-desc");
        sortBy(key, type, currentDir);
      });
    });
  }

  tables.forEach(initTable);
})();
