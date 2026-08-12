// Hobbit Watch front end: filtering + sorting on the set table, and the
// per-card modal (art, price chart, other printings). No dependencies --
// vanilla DOM, a canvas line chart, and card art built straight into a
// Scryfall image URL (no JSON fetch: the same trick ../webapp uses).
(function () {
  "use strict";

  var table = document.getElementById("card-table");
  if (!table) return;

  var rows = Array.prototype.slice.call(table.tBodies[0].rows);
  var searchInput = document.getElementById("search");
  var statusEl = document.getElementById("filter-status");
  var paperOnly = document.getElementById("paper-only");
  var clearBtn = document.getElementById("clear-filters");
  var rarityChips = document.querySelectorAll(".rarity-chip");
  var moveChips = document.querySelectorAll(".move-chip");
  var marketChips = document.querySelectorAll(".market-chip");
  var setChips = document.querySelectorAll(".set-chip");

  var state = { q: "", rarities: [], sets: [], move: "all", market: "tix", paperOnly: false };

  // ------------------------------------------------------------- filtering

  function num(row, key) {
    var v = row.dataset[key];
    return v === "" || v === undefined ? null : parseFloat(v);
  }

  function matches(row) {
    if (state.q && row.dataset.name.indexOf(state.q) === -1) return false;
    if (state.rarities.length && state.rarities.indexOf(row.dataset.rarity) === -1) return false;
    if (state.sets.length && state.sets.indexOf(row.dataset.set) === -1) return false;
    if (state.paperOnly && num(row, "usd") === null) return false;
    if (state.move !== "all") {
      var pct = num(row, state.market === "usd" ? "usdPct" : "tixPct");
      if (pct === null || pct === 0) return false;
      if (state.move === "up" && pct <= 0) return false;
      if (state.move === "down" && pct >= 0) return false;
    }
    return true;
  }

  function applyFilters() {
    var shown = 0;
    rows.forEach(function (row) {
      var ok = matches(row);
      row.hidden = !ok;
      if (ok) shown++;
    });
    var total = statusEl.dataset.total;
    var bits = [];
    if (state.q) bits.push('matching "' + state.q + '"');
    if (state.sets.length) bits.push("in " + state.sets.join(" / "));
    if (state.rarities.length) bits.push(state.rarities.join(" / ").toLowerCase());
    if (state.paperOnly) bits.push("with a physical price");
    if (state.move !== "all") {
      bits.push((state.move === "up" ? "gaining" : "losing") +
                " in " + (state.market === "usd" ? "paper" : "MTGO"));
    }
    statusEl.textContent = bits.length
      ? "Showing " + shown + " of " + total + " cards " + bits.join(", ") + "."
      : "Showing all " + total + " cards.";
  }

  searchInput.addEventListener("input", function () {
    state.q = searchInput.value.trim().toLowerCase();
    applyFilters();
  });

  function toggleChip(chip, list, value) {
    var i = list.indexOf(value);
    if (i === -1) { list.push(value); chip.classList.add("active"); }
    else { list.splice(i, 1); chip.classList.remove("active"); }
    applyFilters();
  }

  rarityChips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      toggleChip(chip, state.rarities, chip.dataset.rarity);
    });
  });

  setChips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      toggleChip(chip, state.sets, chip.dataset.set);
    });
  });

  function exclusive(chips, chip) {
    chips.forEach(function (c) { c.classList.toggle("active", c === chip); });
  }

  moveChips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      state.move = chip.dataset.move;
      exclusive(moveChips, chip);
      applyFilters();
    });
  });

  marketChips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      state.market = chip.dataset.market;
      exclusive(marketChips, chip);
      applyFilters();
    });
  });

  paperOnly.addEventListener("change", function () {
    state.paperOnly = paperOnly.checked;
    applyFilters();
  });

  clearBtn.addEventListener("click", function () {
    state = { q: "", rarities: [], sets: [], move: "all", market: "tix", paperOnly: false };
    searchInput.value = "";
    paperOnly.checked = false;
    rarityChips.forEach(function (c) { c.classList.remove("active"); });
    setChips.forEach(function (c) { c.classList.remove("active"); });
    moveChips.forEach(function (c) { c.classList.toggle("active", c.dataset.move === "all"); });
    marketChips.forEach(function (c) { c.classList.toggle("active", c.dataset.market === "tix"); });
    applyFilters();
  });

  // --------------------------------------------------------------- sorting

  var SORT_KEYS = {
    number: "number", name: "name", rarity: "rarity", set: "set", usd: "usd",
    usd_pct: "usdPct", tix: "tix", tix_pct: "tixPct", versions: "versions"
  };
  var TEXT_SORTS = { name: true, set: true };
  var RARITY_RANK = { Mythic: 4, Rare: 3, Uncommon: 2, Common: 1 };
  var sortChips = document.querySelectorAll(".sort-chip");

  function sortBy(key, desc) {
    var dataKey = SORT_KEYS[key];
    var sorted = rows.slice().sort(function (a, b) {
      var va, vb;
      if (key === "rarity") {
        va = RARITY_RANK[a.dataset.rarity] || 0;
        vb = RARITY_RANK[b.dataset.rarity] || 0;
      } else if (TEXT_SORTS[key]) {
        va = a.dataset[dataKey] || "";
        vb = b.dataset[dataKey] || "";
        return desc ? vb.localeCompare(va) : va.localeCompare(vb);
      } else {
        va = parseFloat(a.dataset[dataKey]);
        vb = parseFloat(b.dataset[dataKey]);
        // Cards with no price in this market always sort last, either way --
        // an unpriced card isn't "cheapest", it's unknown.
        if (isNaN(va) && isNaN(vb)) return 0;
        if (isNaN(va)) return 1;
        if (isNaN(vb)) return -1;
      }
      return desc ? vb - va : va - vb;
    });
    var body = table.tBodies[0];
    sorted.forEach(function (r) { body.appendChild(r); });
    rows = sorted;

    // Keep the header arrows and the sort chips showing the same thing.
    table.querySelectorAll("th").forEach(function (o) {
      o.classList.remove("sorted-asc", "sorted-desc");
    });
    var th = table.querySelector('th[data-key="' + key + '"]');
    if (th) th.classList.add(desc ? "sorted-desc" : "sorted-asc");
    sortChips.forEach(function (c) {
      c.classList.toggle("active", c.dataset.sort === key);
    });
  }

  table.querySelectorAll("th.sortable").forEach(function (th) {
    th.addEventListener("click", function () {
      sortBy(th.dataset.key, !th.classList.contains("sorted-desc"));
    });
  });

  sortChips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      sortBy(chip.dataset.sort, chip.dataset.dir === "desc");
    });
  });

  // ----------------------------------------------------------------- modal

  var modal = document.getElementById("card-modal");
  var modalBody = document.getElementById("modal-body");
  var modalClose = document.getElementById("modal-close");
  var lastFocused = null;
  var cache = {};

  // Art comes from the checked-in Scryfall snapshot when we have it (an exact
  // per-printing image URL); otherwise fall back to Scryfall's image API,
  // which resolves a card by exact name with no JSON call in between.
  function scryfallArt(name, set, size) {
    var url = "https://api.scryfall.com/cards/named?exact=" + encodeURIComponent(name) +
              "&format=image&version=" + (size || "normal");
    if (set) url += "&set=" + encodeURIComponent(set);
    return url;
  }

  function artFor(name, printing, size) {
    if (printing) {
      var direct = size === "small" ? printing.image_small : printing.image;
      if (direct) return direct;
    }
    return scryfallArt(name, printing ? printing.scryfall_set : null, size);
  }

  function fallbackArt(img, name) {
    // A GoatBots set code Scryfall doesn't know (promo buckets, MTGO-only
    // bundles) 404s the set-scoped image; fall back to the card by name once.
    img.addEventListener("error", function () {
      if (img.dataset.fellBack) return;
      img.dataset.fellBack = "1";
      img.src = scryfallArt(name, null, img.dataset.size || "normal");
    });
  }

  function money(v, market) {
    if (v === null || v === undefined) return "—";
    // The sign goes in front of the currency symbol, not between it and the
    // digits: "-$1.20", never "$-1.20".
    var sign = v < 0 ? "-" : "";
    var abs = Math.abs(v).toFixed(2);
    return market === "usd" ? sign + "$" + abs : sign + abs + " tix";
  }

  function deltaHtml(stats, market) {
    if (!stats || stats.change === null) return '<span class="muted">no data yet</span>';
    var cls = stats.change > 0 ? "gain" : (stats.change < 0 ? "loss" : "");
    var sign = stats.change > 0 ? "+" : "";
    var pct = stats.pct === null ? "" : ' <span class="pct">' + sign + stats.pct + "%</span>";
    return '<span class="' + cls + '">' + sign + money(stats.change, market) + pct + "</span>";
  }

  // `current` is the live Scryfall price for this printing, used as the
  // headline figure when our archive has no series for the card yet.
  function priceBox(title, stats, market, current) {
    var cls = stats.change > 0 ? " gain-box" : (stats.change < 0 ? " loss-box" : "");
    var headline = stats.last;
    var fromScryfall = false;
    if (headline === null && current !== null && current !== undefined) {
      headline = current;
      fromScryfall = true;
    }
    var since = stats.first_date
      ? "since " + stats.first_date + " (" + money(stats.first, market) + ", " +
        stats.days + " day" + (stats.days === 1 ? "" : "s") + ")"
      : (fromScryfall ? "Scryfall, current — no archived history yet"
                      : "no archived data yet");
    return '<div class="price-box' + (stats.change === null ? "" : cls) + '">' +
      "<h3>" + title + "</h3>" +
      '<div class="value">' + money(headline, market) + "</div>" +
      '<div class="delta">' + deltaHtml(stats, market) + "</div>" +
      '<span class="since">' + since + "</span></div>";
  }

  // Scryfall names treatments in slug form ("bookcover", "extendedart");
  // spell out the ones a reader would recognize on sight.
  var TREATMENT_LABELS = {
    bookcover: "book cover", extendedart: "extended art", showcase: "showcase",
    borderless: "borderless", serialized: "serialized", surgefoil: "surge foil",
    galaxyfoil: "galaxy foil", textured: "textured", halofoil: "halo foil",
    boosterfun: "booster fun", promo: "promo", prerelease: "prerelease",
    stepandcompleat: "step-and-compleat", concept: "concept art",
    fullart: "full art", inverted: "inverted", raisedfoil: "raised foil"
  };

  function treatmentText(v) {
    var seen = {};
    return (v.treatments || []).map(function (t) {
      return TREATMENT_LABELS[t] || t.replace(/([a-z])([A-Z])/g, "$1 $2");
    }).filter(function (label) {
      if (seen[label]) return false;
      seen[label] = true;
      return true;
    }).join(", ");
  }

  function versionHtml(card, v) {
    var img = artFor(card.name, v, "small");
    var meta = [v.set_name || v.set, v.number ? "#" + v.number : "",
                v.released ? v.released.slice(0, 4) : "", v.digital ? "MTGO" : ""]
      .filter(Boolean).join(" · ");
    var treatment = treatmentText(v);
    // Paper price where the printing has one, digital where our archive does;
    // a printing can legitimately have either, both, or neither.
    var prices = [];
    if (v.usd !== null && v.usd !== undefined) {
      prices.push('<span class="version-usd">' + money(v.usd, "usd") +
                  (v.usd_finish === "usd_foil" ? " foil" : "") +
                  (v.usd_stats && v.usd_stats.change !== null
                    ? " " + deltaHtml(v.usd_stats, "usd") : "") + "</span>");
    }
    var tixNow = v.tix_stats.last !== null ? v.tix_stats.last : v.scry_tix;
    if (tixNow !== null && tixNow !== undefined) {
      prices.push('<span class="version-tix">' + money(tixNow, "tix") +
                  (v.tix_stats.change !== null ? " " + deltaHtml(v.tix_stats, "tix") : "") +
                  "</span>");
    }
    if (!prices.length) prices.push('<span class="muted">no price</span>');

    var title = escapeHtml(card.name) + " (" + escapeHtml(v.set_name || v.set) + ")";
    var art = '<img loading="lazy" alt="' + title + '" data-size="small"' +
      ' data-card-name="' + escapeHtml(card.name) + '"' +
      ' data-set="' + escapeHtml(v.scryfall_set || "") + '" src="' + img + '">';
    if (v.scryfall_uri) {
      art = '<a href="' + escapeHtml(v.scryfall_uri) + '" target="_blank" rel="noopener">' +
            art + "</a>";
    }
    return '<div class="version-card' + (v.same_set ? " version-same-set" : "") + '">' +
      art +
      '<div class="version-set">' + escapeHtml(v.set) +
      (v.same_set ? ' <span class="version-badge">this set</span>' : "") + "</div>" +
      (treatment ? '<div class="version-treatment">' + escapeHtml(treatment) + "</div>" : "") +
      '<div class="version-meta">' + escapeHtml(meta) + "</div>" +
      '<div class="version-price">' + prices.join('<br>') + "</div>" +
      '<canvas class="version-spark" data-key="' + escapeHtml(v.key) + '"></canvas>' +
      "</div>";
  }

  // Version keys are "SET-collectornumber" (or "gb-<id>"), and collector
  // numbers carry stars and letters -- escape before using one in a selector.
  function cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/["\\\]]/g, "\\$&");
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function renderCard(card) {
    var versions = card.versions || [];
    var versionsSection;
    if (!versions.length) {
      versionsSection =
        '<div class="versions-block"><h3>Other versions</h3>' +
        '<p class="versions-note">No other printing of this card exists yet &mdash; ' +
        "it's new to " + escapeHtml(card.set_name || card.set) + ".</p></div>";
    } else {
      var hidden = card.versions_total - versions.length;
      versionsSection =
        '<div class="versions-block"><h3>Other versions</h3>' +
        '<p class="versions-note">' + card.versions_total + " other MTGO printing" +
        (card.versions_total === 1 ? "" : "s") + " of this card" +
        (hidden > 0 ? ", the " + versions.length + " most expensive shown" : "") +
        ". Each price is that printing's own &mdash; paper first, then MTGO where " +
        "the printing exists there; the spark line is its recent tix history.</p>" +
        '<div class="versions-grid">' +
        versions.map(function (v) { return versionHtml(card, v); }).join("") +
        "</div></div>";
    }

    var usdNote;
    if (card.usd.length) {
      usdNote = card.usd_basis === "printing"
        ? "Physical history is MTGJSON's archived daily snapshots for this exact printing."
        : "Physical history is MTGJSON's archived daily snapshots, as a median across " +
          "every printing of this card name &mdash; the exact printing couldn't be " +
          "identified in the uuid map.";
      if (card.scry_usd !== null && card.scry_usd !== undefined) {
        usdNote += " Scryfall's current price: " + money(card.scry_usd, "usd") +
          (card.scry_usd_foil ? " (" + money(card.scry_usd_foil, "usd") + " foil)" : "") + ".";
      }
    } else {
      usdNote = "The physical price is Scryfall's current one for this printing. " +
        "No archived MTGJSON history for it yet, so there's nothing to chart or " +
        "measure a change against.";
    }

    var sub = [card.set_name || card.set,
               card.number ? "#" + card.number : "",
               card.rarity,
               card.released ? "released " + card.released : ""].filter(Boolean).join(" · ");

    modalBody.innerHTML =
      '<div class="modal-head">' +
      '<img class="modal-art" data-size="normal" data-card-name="' + escapeHtml(card.name) + '"' +
      ' data-set="' + escapeHtml(card.scryfall_set || "") + '"' +
      ' alt="' + escapeHtml(card.name) + '" src="' +
      (card.art || scryfallArt(card.name, card.scryfall_set)) + '">' +
      '<div class="modal-head-text">' +
      '<h2 id="modal-title">' +
      (card.scryfall_uri
        ? '<a href="' + escapeHtml(card.scryfall_uri) + '" target="_blank" rel="noopener">' +
          escapeHtml(card.name) + "</a>"
        : escapeHtml(card.name)) + "</h2>" +
      '<p class="modal-sub">' + escapeHtml(sub) + "</p>" +
      '<div class="price-grid">' +
      priceBox("Physical (USD)", card.usd_stats, "usd", card.scry_usd) +
      priceBox("Digital (tix)", card.tix_stats, "tix", card.scry_tix) +
      "</div>" +
      '<p class="chart-note">' + usdNote + "</p>" +
      "</div></div>" +
      '<div class="chart-block">' +
      '<div class="chart-head"><h3>Price history</h3>' +
      '<div class="chart-toggle">' +
      '<button type="button" class="chip market-toggle active" data-series="tix">Digital (tix)</button>' +
      '<button type="button" class="chip market-toggle" data-series="usd">Physical ($)</button>' +
      "</div></div>" +
      '<div class="chart-wrap"><canvas class="chart-canvas" id="card-chart"></canvas>' +
      '<div class="chart-tooltip" id="chart-tooltip"></div></div>' +
      '<p class="chart-note" id="chart-caption"></p>' +
      "</div>" +
      versionsSection;

    modalBody.querySelectorAll("img[data-card-name]").forEach(function (img) {
      fallbackArt(img, img.dataset.cardName);
    });

    setupChart(card);
    versions.forEach(function (v) {
      var canvas = modalBody.querySelector('canvas[data-key="' + cssEscape(v.key) + '"]');
      if (canvas) drawSparkline(canvas, v.tix);
    });
  }

  // ----------------------------------------------------------------- chart

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function setupChart(card) {
    var canvas = document.getElementById("card-chart");
    var caption = document.getElementById("chart-caption");
    var tooltip = document.getElementById("chart-tooltip");
    var current = card.tix.length ? "tix" : "usd";
    var points = [];

    function series() { return current === "tix" ? card.tix : card.usd; }

    function draw() {
      var data = series();
      var dpr = window.devicePixelRatio || 1;
      var w = canvas.clientWidth || 640;
      var h = 230;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.height = h + "px";
      var ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      points = [];

      var border = cssVar("--border", "#ddd6c9");
      var muted = cssVar("--muted", "#6b6b6b");
      var fg = cssVar("--fg", "#232323");
      var up = cssVar("--gain", "#2c7a3f");
      var down = cssVar("--loss", "#b0393f");
      var flat = cssVar("--accent", "#3f6132");

      if (!data.length) {
        ctx.fillStyle = muted;
        ctx.font = "14px sans-serif";
        ctx.fillText("No " + (current === "tix" ? "digital" : "physical") +
                     " price history archived for this card yet.", 12, h / 2);
        caption.textContent = "";
        return;
      }

      var padL = 58, padR = 16, padT = 16, padB = 30;
      var plotW = w - padL - padR;
      var plotH = h - padT - padB;
      var values = data.map(function (p) { return p[1]; });
      var maxV = Math.max.apply(null, values);
      var minV = Math.min.apply(null, values);
      // Pad the band so a flat or barely-moving series doesn't render as a
      // line pinned to an edge of the plot.
      var span = (maxV - minV) || Math.max(maxV * 0.2, 0.1);
      var top = maxV + span * 0.2;
      var bottom = Math.max(0, minV - span * 0.2);

      var t0 = Date.parse(data[0][0]);
      var t1 = Date.parse(data[data.length - 1][0]);

      function xPix(t) { return padL + ((t - t0) / ((t1 - t0) || 1)) * plotW; }
      function yPix(v) { return padT + plotH - ((v - bottom) / ((top - bottom) || 1)) * plotH; }

      ctx.strokeStyle = border;
      ctx.fillStyle = muted;
      ctx.font = "11px sans-serif";
      ctx.lineWidth = 1;
      for (var i = 0; i <= 4; i++) {
        var v = bottom + ((top - bottom) * i) / 4;
        var y = Math.round(yPix(v)) + 0.5;
        ctx.beginPath();
        ctx.moveTo(padL, y);
        ctx.lineTo(padL + plotW, y);
        ctx.stroke();
        var label = current === "usd" ? "$" + v.toFixed(2) : v.toFixed(2);
        ctx.fillText(label, 6, y + 3.5);
      }

      // x labels: first and last archived day (short series, no need for more)
      ctx.fillText(data[0][0], padL, h - 10);
      var lastLabel = data[data.length - 1][0];
      ctx.fillText(lastLabel, padL + plotW - ctx.measureText(lastLabel).width, h - 10);

      var change = values[values.length - 1] - values[0];
      var color = change > 0 ? up : (change < 0 ? down : flat);

      if (data.length === 1) {
        var cx = padL + plotW / 2, cy = yPix(values[0]);
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(cx, cy, 5, 0, Math.PI * 2);
        ctx.fill();
        points.push({ x: cx, y: cy, date: data[0][0], value: values[0] });
      } else {
        ctx.beginPath();
        data.forEach(function (p, i) {
          var x = xPix(Date.parse(p[0])), y = yPix(p[1]);
          points.push({ x: x, y: y, date: p[0], value: p[1] });
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();

        // soft fill under the line
        ctx.lineTo(points[points.length - 1].x, padT + plotH);
        ctx.lineTo(points[0].x, padT + plotH);
        ctx.closePath();
        ctx.globalAlpha = 0.10;
        ctx.fillStyle = color;
        ctx.fill();
        ctx.globalAlpha = 1;

        ctx.fillStyle = color;
        points.forEach(function (p) {
          ctx.beginPath();
          ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2);
          ctx.fill();
        });
      }

      ctx.fillStyle = fg;
      var pct = values[0] ? ((change / values[0]) * 100).toFixed(1) : null;
      caption.textContent =
        data.length + " archived day" + (data.length === 1 ? "" : "s") + " · " +
        money(values[0], current) + " on " + data[0][0] + " → " +
        money(values[values.length - 1], current) + " on " + data[data.length - 1][0] +
        (pct === null ? "" : " (" + (change > 0 ? "+" : "") + pct + "%)");
    }

    canvas.addEventListener("mousemove", function (e) {
      if (!points.length) return;
      var rect = canvas.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var nearest = points[0];
      points.forEach(function (p) {
        if (Math.abs(p.x - x) < Math.abs(nearest.x - x)) nearest = p;
      });
      tooltip.textContent = nearest.date + ": " + money(nearest.value, current);
      tooltip.style.left = nearest.x + "px";
      tooltip.style.top = nearest.y + "px";
      tooltip.classList.add("visible");
    });
    canvas.addEventListener("mouseleave", function () {
      tooltip.classList.remove("visible");
    });

    modalBody.querySelectorAll(".market-toggle").forEach(function (btn) {
      if (btn.dataset.series === current) btn.classList.add("active");
      else btn.classList.remove("active");
      btn.addEventListener("click", function () {
        current = btn.dataset.series;
        modalBody.querySelectorAll(".market-toggle").forEach(function (o) {
          o.classList.toggle("active", o === btn);
        });
        draw();
      });
    });

    draw();
    window.addEventListener("resize", draw);
  }

  function drawSparkline(canvas, data) {
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth || 130;
    var h = 28;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!data || data.length < 2) return;
    var values = data.map(function (p) { return p[1]; });
    var maxV = Math.max.apply(null, values);
    var minV = Math.min.apply(null, values);
    var span = (maxV - minV) || 1;
    var change = values[values.length - 1] - values[0];
    ctx.strokeStyle = change > 0 ? cssVar("--gain", "#2c7a3f")
                    : (change < 0 ? cssVar("--loss", "#b0393f") : cssVar("--muted", "#6b6b6b"));
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    data.forEach(function (p, i) {
      var x = (i / (data.length - 1)) * (w - 2) + 1;
      var y = h - 3 - ((p[1] - minV) / span) * (h - 6);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  // ------------------------------------------------------------ modal open

  function openCard(slug, trigger) {
    lastFocused = trigger || document.activeElement;
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    modalClose.focus();
    if (cache[slug]) {
      renderCard(cache[slug]);
      return;
    }
    modalBody.innerHTML = '<p class="modal-loading">Loading&hellip;</p>';
    fetch("api/card/" + encodeURIComponent(slug) + ".json")
      .then(function (r) {
        if (!r.ok) throw new Error("not found");
        return r.json();
      })
      .then(function (card) {
        cache[slug] = card;
        renderCard(card);
      })
      .catch(function () {
        modalBody.innerHTML = '<p class="modal-loading">Could not load this card.</p>';
      });
  }

  function closeModal() {
    modal.hidden = true;
    document.body.style.overflow = "";
    if (lastFocused) lastFocused.focus();
  }

  rows.forEach(function (row) {
    row.addEventListener("click", function () { openCard(row.dataset.slug, row); });
    row.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openCard(row.dataset.slug, row);
      }
    });
  });

  modalClose.addEventListener("click", closeModal);
  modal.addEventListener("click", function (e) {
    if (e.target === modal) closeModal();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !modal.hidden) closeModal();
  });

  applyFilters();
})();
