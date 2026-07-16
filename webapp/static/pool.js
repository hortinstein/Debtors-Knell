// Card Pool Builder page: live title filter + selected-count readout.
(function () {
  "use strict";

  var table = document.getElementById("pool-table");
  var filterInput = document.getElementById("pool-filter");
  var countEl = document.getElementById("pool-count");
  var selectAllBtn = document.getElementById("pool-select-all");
  var selectNoneBtn = document.getElementById("pool-select-none");
  if (!table) return;

  var rows = Array.prototype.slice.call(table.tBodies[0].querySelectorAll("tr"));
  var checkboxes = rows.map(function (row) { return row.querySelector('input[type="checkbox"]'); });

  if (filterInput) {
    filterInput.addEventListener("input", function () {
      var q = filterInput.value.trim().toLowerCase();
      rows.forEach(function (row) {
        var title = row.getAttribute("data-title") || "";
        row.style.display = !q || title.indexOf(q) !== -1 ? "" : "none";
      });
    });
  }

  function updateCount() {
    if (!countEl) return;
    var n = table.querySelectorAll('input[type="checkbox"]:checked').length;
    countEl.textContent = n;
  }

  table.addEventListener("change", function (evt) {
    if (evt.target.matches('input[type="checkbox"]')) updateCount();
  });

  if (selectAllBtn) {
    selectAllBtn.addEventListener("click", function () {
      checkboxes.forEach(function (cb) { cb.checked = true; });
      updateCount();
    });
  }
  if (selectNoneBtn) {
    selectNoneBtn.addEventListener("click", function () {
      checkboxes.forEach(function (cb) { cb.checked = false; });
      updateCount();
    });
  }
})();
