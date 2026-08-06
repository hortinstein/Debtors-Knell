// Card Stats page: live name filter over the full card table, plus a scope
// toggle that switches the deck/copy counts between the column's own budget
// builds and the reference lists the articles merely quote (tournament decks,
// reader submissions, preconstructed decks, non-budget builds). Both counts
// are rendered into data- attributes server-side, so switching scope is a
// relabel-and-resort rather than a refetch -- which keeps the page working in
// the frozen static build.
(function () {
  "use strict";

  var table = document.getElementById("stats-table");
  var filterInput = document.getElementById("stats-filter");
  if (!table) return;

  var rows = Array.prototype.slice.call(table.tBodies[0].querySelectorAll("tr"));
  var scopeInputs = Array.prototype.slice.call(
    document.querySelectorAll('input[name="stats-scope"]')
  );

  function currentScope() {
    var checked = scopeInputs.filter(function (i) { return i.checked; })[0];
    return checked ? checked.value : "budget";
  }

  function applyAll() {
    var q = filterInput ? filterInput.value.trim().toLowerCase() : "";
    var scope = currentScope();
    rows.forEach(function (row) {
      var decks = row.getAttribute("data-decks-" + scope) || "0";
      var qty = row.getAttribute("data-qty-" + scope) || "0";
      // Keep the sort keys in step with what's on screen, so clicking
      // "In # Decks" sorts by the scope you're actually looking at.
      row.setAttribute("data-decks", decks);
      row.setAttribute("data-qty", qty);
      var decksCell = row.querySelector(".col-decks");
      var qtyCell = row.querySelector(".col-qty");
      if (decksCell) decksCell.textContent = decks;
      if (qtyCell) qtyCell.textContent = qty;

      var name = row.getAttribute("data-name") || "";
      // A card with no appearances in the selected scope isn't part of that
      // ranking at all -- hide it rather than show a wall of zeroes.
      var inScope = decks !== "0";
      row.style.display = inScope && (!q || name.indexOf(q) !== -1) ? "" : "none";
    });
    table.setAttribute("data-scope", scope);
  }

  if (filterInput) filterInput.addEventListener("input", applyAll);
  scopeInputs.forEach(function (input) { input.addEventListener("change", applyAll); });

  applyAll();
})();
