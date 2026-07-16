// Vanilla-JS sortable table + click-to-open rows for the index page.
// No external dependencies.
(function () {
  "use strict";

  var table = document.getElementById("deck-table");
  if (!table) return;

  // Clicking anywhere on a row (outside of a link) navigates to the deck.
  var tbody = table.tBodies[0];
  tbody.addEventListener("click", function (evt) {
    if (evt.target.closest("a")) return; // let real links behave normally
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
})();
