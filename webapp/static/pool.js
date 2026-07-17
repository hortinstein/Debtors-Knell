// Card Pool Builder page: live title filter, selected-count readout, and
// fully client-side aggregation (this page has to work with no server --
// see /pool-data.json, fetched once here -- since arbitrary deck-selection
// combinations aren't something a static site build can pre-render one page
// per).
(function () {
  "use strict";

  var form = document.getElementById("pool-form");
  var table = document.getElementById("pool-table");
  var filterInput = document.getElementById("pool-filter");
  var countEl = document.getElementById("pool-count");
  var selectAllBtn = document.getElementById("pool-select-all");
  var selectNoneBtn = document.getElementById("pool-select-none");
  var resultsEl = document.getElementById("pool-results");
  if (!table || !form) return;

  var rows = Array.prototype.slice.call(table.tBodies[0].querySelectorAll("tr"));
  var checkboxes = rows.map(function (row) { return row.querySelector('input[type="checkbox"]'); });

  var poolDataById = null;
  fetch(form.dataset.poolDataUrl)
    .then(function (r) { return r.json(); })
    .then(function (decks) {
      poolDataById = {};
      decks.forEach(function (d) { poolDataById[d.id] = d; });
      restoreFromLocation();
    });

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

  function selectedIds() {
    return checkboxes.filter(function (cb) { return cb.checked; }).map(function (cb) { return cb.value; });
  }

  function currentMode() {
    var checked = form.querySelector('input[name="mode"]:checked');
    return checked ? checked.value : "sum";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function money(v) {
    return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // Mirrors the old server-side _aggregate_card_pool: sum mode totals every
  // selected deck's need for a card; shared mode takes the max, on the
  // assumption the same physical copies get reshuffled between decks.
  function aggregate(ids, mode) {
    var byName = {};
    var order = [];
    ids.forEach(function (id) {
      var deck = poolDataById[id];
      if (!deck) return;
      deck.cards.forEach(function (r) {
        var entry = byName[r.name];
        if (!entry) {
          entry = { name: r.name, sumQty: 0, maxQty: 0, unitUsd: null, unitTix: null, decks: [] };
          byName[r.name] = entry;
          order.push(r.name);
        }
        entry.sumQty += r.qty;
        entry.maxQty = Math.max(entry.maxQty, r.qty);
        if (entry.unitUsd === null && r.unit_usd !== null) entry.unitUsd = r.unit_usd;
        if (entry.unitTix === null && r.unit_tix !== null) entry.unitTix = r.unit_tix;
        entry.decks.push({ title: deck.title, label: deck.label, qty: r.qty });
      });
    });
    var names = order.slice().sort(function (a, b) {
      return a.toLowerCase().localeCompare(b.toLowerCase());
    });
    return names.map(function (name) {
      var e = byName[name];
      var needed = mode === "shared" ? e.maxQty : e.sumQty;
      return {
        name: name,
        qty: needed,
        unitUsd: e.unitUsd,
        unitTix: e.unitTix,
        extUsd: e.unitUsd !== null ? needed * e.unitUsd : null,
        extTix: e.unitTix !== null ? needed * e.unitTix : null,
        decks: e.decks,
      };
    });
  }

  function wireDownload(poolRows) {
    var downloadLink = document.getElementById("pool-download-link");
    if (!downloadLink) return;
    downloadLink.addEventListener("click", function (evt) {
      evt.preventDefault();
      var text = poolRows.map(function (r) { return r.qty + " " + r.name; }).join("\n") + "\n";
      var blob = new Blob([text], { type: "text/plain" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = "card_pool.txt";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    });
  }

  function renderResults(ids, mode) {
    if (!poolDataById) return;
    if (!ids.length) {
      resultsEl.hidden = true;
      resultsEl.innerHTML = "";
      return;
    }
    var poolRows = aggregate(ids, mode);
    var usdTotal = 0, tixTotal = 0;
    poolRows.forEach(function (r) {
      if (r.extUsd !== null) usdTotal += r.extUsd;
      if (r.extTix !== null) tixTotal += r.extTix;
    });

    var modeNote = mode === "shared"
      ? "Shared-pool mode: quantities are the <em>most</em> any single selected deck needs of a " +
        "card, on the assumption you only build one deck at a time and reshuffle the same cards " +
        "into the next one."
      : "Build-all-at-once mode: quantities are <em>summed</em> across every selected deck, on the " +
        "assumption you want them all assembled simultaneously with no cards shared between them.";

    var rowsHtml = poolRows.map(function (r) {
      var decksList = r.decks.map(function (dd) {
        var label = dd.label && dd.label !== "Decklist" ? " (" + escapeHtml(dd.label) + ")" : "";
        return escapeHtml(dd.title) + label + " ×" + dd.qty;
      }).join(", ");
      return (
        "<tr>" +
        "<td>" + r.qty + "</td>" +
        "<td>" + escapeHtml(r.name) + "</td>" +
        "<td>" + (r.unitUsd !== null ? "$" + money(r.unitUsd) : "N/A") + "</td>" +
        "<td>" + (r.extUsd !== null ? "$" + money(r.extUsd) : "N/A") + "</td>" +
        "<td>" + (r.unitTix !== null ? money(r.unitTix) : "N/A") + "</td>" +
        "<td>" + (r.extTix !== null ? money(r.extTix) : "N/A") + "</td>" +
        "<td class=\"pool-decks-cell\">" + r.decks.length + " deck" + (r.decks.length !== 1 ? "s" : "") +
        "<div class=\"pool-decks-list\">" + decksList + "</div></td>" +
        "</tr>"
      );
    }).join("");

    resultsEl.innerHTML =
      "<h2>Combined shopping list &mdash; " + ids.length + " deck" + (ids.length !== 1 ? "s" : "") + "</h2>" +
      "<p class=\"muted pool-mode-note\">" + modeNote + "</p>" +
      "<p class=\"combined-total\">" +
      "<strong>Grand total:</strong> $" + money(usdTotal) + " physical &middot; " + money(tixTotal) +
      " tix digital &middot; " +
      "<a class=\"download-link\" href=\"#\" id=\"pool-download-link\">&#8681; Download shopping list (.txt)</a>" +
      "</p>" +
      "<div class=\"priced-table\"><table><thead><tr>" +
      "<th>Qty</th><th>Card</th><th>Unit</th><th>Extended</th><th>Unit (tix)</th><th>Extended (tix)</th><th>Needed in</th>" +
      "</tr></thead><tbody>" + rowsHtml + "</tbody></table></div>";

    resultsEl.hidden = false;
    wireDownload(poolRows);
  }

  function syncLocation(ids, mode) {
    var params = new URLSearchParams();
    ids.forEach(function (id) { params.append("deck", id); });
    if (mode !== "sum") params.set("mode", mode);
    var qs = params.toString();
    window.history.replaceState(null, "", window.location.pathname + (qs ? "?" + qs : ""));
  }

  function build() {
    var ids = selectedIds();
    var mode = currentMode();
    syncLocation(ids, mode);
    renderResults(ids, mode);
  }

  form.addEventListener("submit", function (evt) {
    evt.preventDefault();
    build();
  });

  function restoreFromLocation() {
    var params = new URLSearchParams(window.location.search);
    var ids = params.getAll("deck");
    if (!ids.length) return;
    var mode = params.get("mode") === "shared" ? "shared" : "sum";
    var idSet = {};
    ids.forEach(function (id) { idSet[id] = true; });
    checkboxes.forEach(function (cb) { cb.checked = !!idSet[cb.value]; });
    var modeInput = form.querySelector('input[name="mode"][value="' + mode + '"]');
    if (modeInput) modeInput.checked = true;
    updateCount();
    renderResults(ids, mode);
  }
})();
