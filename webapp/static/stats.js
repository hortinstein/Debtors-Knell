// Card Stats page: live name filter over the full card table.
(function () {
  "use strict";

  var table = document.getElementById("stats-table");
  var filterInput = document.getElementById("stats-filter");
  if (!table || !filterInput) return;

  var rows = Array.prototype.slice.call(table.tBodies[0].querySelectorAll("tr"));

  filterInput.addEventListener("input", function () {
    var q = filterInput.value.trim().toLowerCase();
    rows.forEach(function (row) {
      var name = row.getAttribute("data-name") || "";
      row.style.display = !q || name.indexOf(q) !== -1 ? "" : "none";
    });
  });
})();
