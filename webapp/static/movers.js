// Weekly Price Movers page: renders the top-20 list client-side from the
// embedded #movers-data JSON (see scripts/build_movers.py for the format),
// with market (paper/tix) and ranking (% / absolute) toggles, per-card
// paper+digital sparkline charts, and the Debtors' Knell landing modal.
// Vanilla canvas, no external dependencies (same approach as price-chart.js).
(function () {
  "use strict";

  var dataEl = document.getElementById("movers-data");
  var listEl = document.getElementById("movers-list");
  if (!dataEl || !listEl) return;

  var data;
  try {
    data = JSON.parse(dataEl.textContent);
  } catch (e) {
    return;
  }

  var state = { market: "paper", metric: "pct" };

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function fmtMoney(v, market) {
    if (market === "tix") {
      return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " tix";
    }
    return "$" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtChange(m, market) {
    var arrow = m.change >= 0 ? "▲" : "▼";
    var sign = m.change >= 0 ? "+" : "−";
    var abs = Math.abs(m.change);
    var pct = Math.abs(m.pct);
    return arrow + " " + sign + fmtMoney(abs, market) + " (" + sign + pct.toFixed(1) + "%)";
  }

  function scryfallImg(name) {
    // fuzzy (not exact): GoatBots/MTGJSON names occasionally differ from
    // Scryfall's in punctuation or face-naming; fuzzy absorbs most of that.
    return "https://api.scryfall.com/cards/named?fuzzy=" +
      encodeURIComponent(name) + "&format=image&version=small";
  }

  function scryfallSearch(name) {
    return "https://scryfall.com/search?q=" + encodeURIComponent('!"' + name + '"');
  }

  // ------------------------------------------------------------------
  // Sparkline: one week of one market's prices on a small canvas
  // ------------------------------------------------------------------
  function drawSparkline(canvas, series, market) {
    var dpr = window.devicePixelRatio || 1;
    var W = canvas.clientWidth || 260;
    var H = canvas.clientHeight || 74;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    var muted = cssVar("--muted") || "#6b6b6b";
    if (!series || series.length < 2) {
      ctx.fillStyle = muted;
      ctx.font = "11px sans-serif";
      ctx.fillText(series && series.length === 1 ? "only one day of data" : "no data this week", 8, H / 2 + 4);
      return;
    }

    var up = series[series.length - 1][1] >= series[0][1];
    var color = cssVar(up ? "--gain" : "--loss") || (up ? "#2c7a3f" : "#b0393f");

    var padL = 6, padR = 6, padT = 15, padB = 15;
    var values = series.map(function (p) { return p[1]; });
    var minV = Math.min.apply(null, values);
    var maxV = Math.max.apply(null, values);
    if (maxV === minV) { maxV += 0.01; minV -= 0.01; }
    var t0 = new Date(series[0][0] + "T00:00:00Z").getTime();
    var t1 = new Date(series[series.length - 1][0] + "T00:00:00Z").getTime();

    function x(p) {
      var t = new Date(p[0] + "T00:00:00Z").getTime();
      return padL + ((t - t0) / (t1 - t0 || 1)) * (W - padL - padR);
    }
    function y(p) {
      return padT + (1 - (p[1] - minV) / (maxV - minV)) * (H - padT - padB);
    }

    // area fill
    ctx.beginPath();
    series.forEach(function (p, i) {
      if (i === 0) ctx.moveTo(x(p), y(p)); else ctx.lineTo(x(p), y(p));
    });
    ctx.lineTo(x(series[series.length - 1]), H - padB + 8);
    ctx.lineTo(x(series[0]), H - padB + 8);
    ctx.closePath();
    ctx.globalAlpha = 0.14;
    ctx.fillStyle = color;
    ctx.fill();
    ctx.globalAlpha = 1;

    // line
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    series.forEach(function (p, i) {
      if (i === 0) ctx.moveTo(x(p), y(p)); else ctx.lineTo(x(p), y(p));
    });
    ctx.stroke();

    // endpoint dots + value labels
    var first = series[0], last = series[series.length - 1];
    ctx.fillStyle = color;
    [first, last].forEach(function (p) {
      ctx.beginPath();
      ctx.arc(x(p), y(p), 2.8, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.font = "10px sans-serif";
    ctx.fillStyle = muted;
    ctx.textAlign = "left";
    ctx.fillText(fmtMoney(first[1], market), padL, y(first) < H / 2 ? H - 4 : 11);
    ctx.textAlign = "right";
    ctx.fillStyle = color;
    ctx.fillText(fmtMoney(last[1], market), W - padR, y(last) < H / 2 ? H - 4 : 11);
    ctx.textAlign = "start";
  }

  // ------------------------------------------------------------------
  // List rendering
  // ------------------------------------------------------------------
  function el(tag, className, text) {
    var e = document.createElement(tag);
    if (className) e.className = className;
    if (text != null) e.textContent = text;
    return e;
  }

  function chartBlock(label, series, market) {
    var wrap = el("div", "mover-chart");
    wrap.appendChild(el("span", "mover-chart-label", label));
    var canvas = document.createElement("canvas");
    canvas.className = "mover-chart-canvas";
    wrap.appendChild(canvas);
    // draw after insertion so clientWidth is real
    requestAnimationFrame(function () { drawSparkline(canvas, series, market); });
    return wrap;
  }

  function render() {
    var viewKey = state.market + "_" + state.metric;
    var names = (data.views && data.views[viewKey]) || [];
    listEl.innerHTML = "";

    var note = document.getElementById("movers-view-note");
    if (note) {
      var marketLabel = state.market === "paper" ? "paper (USD)" : "MTGO (tix)";
      var noteText = state.metric === "pct"
        ? "Ranked by largest percentage change in " + marketLabel + " over the week; cards starting under " +
          (state.market === "paper" ? fmtMoney(data.min_usd_for_pct || 1, "paper") : fmtMoney(data.min_tix_for_pct || 0.5, "tix")) +
          " are excluded to keep bulk-bin noise out."
        : "Ranked by largest absolute " + (state.market === "paper" ? "dollar" : "tix") +
          " change in " + marketLabel + " over the week.";
      note.textContent = noteText + " Gainers in green, losers in red.";
    }

    if (!names.length) {
      listEl.appendChild(el("li", "mover-empty",
        "No " + (state.market === "paper" ? "paper" : "MTGO") +
        " price data was available for this day's window."));
      return;
    }

    names.forEach(function (name, i) {
      var card = data.cards[name] || { name: name };
      var m = card[state.market];  // the ranked market's summary + series
      var other = state.market === "paper" ? card.tix : card.paper;

      var li = el("li", "mover-row");

      li.appendChild(el("div", "mover-rank", String(i + 1)));

      var thumbWrap = el("div", "mover-thumb-wrap");
      var img = document.createElement("img");
      img.className = "mover-thumb";
      img.loading = "lazy";
      img.alt = name;
      img.src = scryfallImg(name);
      img.onerror = function () {
        var ph = el("div", "mover-thumb-placeholder", name);
        thumbWrap.replaceChild(ph, img);
      };
      thumbWrap.appendChild(img);
      li.appendChild(thumbWrap);

      var main = el("div", "mover-main");
      var nameEl = el("div", "mover-name");
      var link = el("a", null, name);
      link.href = scryfallSearch(name);
      link.target = "_blank";
      link.rel = "noopener";
      nameEl.appendChild(link);
      main.appendChild(nameEl);

      var subBits = [];
      if (card.paper) subBits.push("Paper " + fmtMoney(card.paper.end, "paper"));
      if (card.tix) subBits.push("MTGO " + fmtMoney(card.tix.end, "tix"));
      main.appendChild(el("div", "mover-sub", subBits.join(" · ")));

      var metric = el("div", "mover-metric " + (m && m.change >= 0 ? "gain" : "loss"));
      if (m) {
        metric.appendChild(el("div", "mover-metric-price", fmtMoney(m.end, state.market)));
        metric.appendChild(el("div", "mover-metric-change", fmtChange(m, state.market)));
        metric.appendChild(el("div", "mover-metric-label",
          (state.market === "paper" ? "paper" : "MTGO") + ", past week"));
      }
      main.appendChild(metric);
      li.appendChild(main);

      var charts = el("div", "mover-charts");
      charts.appendChild(chartBlock("Paper (USD)", card.paper && card.paper.series, "paper"));
      charts.appendChild(chartBlock("MTGO (tix)", card.tix && card.tix.series, "tix"));
      li.appendChild(charts);

      listEl.appendChild(li);
    });
  }

  // toggles
  document.querySelectorAll(".movers-toggle .toggle-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var market = btn.getAttribute("data-market");
      var metric = btn.getAttribute("data-metric");
      if (market) state.market = market;
      if (metric) state.metric = metric;
      var group = btn.parentElement;
      group.querySelectorAll(".toggle-btn").forEach(function (b) {
        b.classList.toggle("active", b === btn);
      });
      render();
    });
  });

  // date-picker navigation
  var dateSelect = document.getElementById("movers-date-select");
  if (dateSelect) {
    dateSelect.addEventListener("change", function () {
      if (dateSelect.value) window.location.href = dateSelect.value;
    });
  }

  var redrawTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(redrawTimer);
    redrawTimer = setTimeout(render, 150);
  });
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
  }

  // ------------------------------------------------------------------
  // Debtors' Knell landing modal
  // ------------------------------------------------------------------
  var modal = document.getElementById("movers-modal");
  var SEEN_KEY = "debtorsKnellMoversIntroSeen";

  function openModal() {
    if (modal) {
      modal.hidden = false;
      document.body.classList.add("movers-modal-open");
    }
  }
  function closeModal() {
    if (modal) {
      modal.hidden = true;
      document.body.classList.remove("movers-modal-open");
    }
    try { localStorage.setItem(SEEN_KEY, "1"); } catch (e) { /* private mode */ }
  }

  if (modal) {
    var seen = null;
    try { seen = localStorage.getItem(SEEN_KEY); } catch (e) { /* private mode */ }
    if (seen !== "1") openModal();

    var closeBtn = document.getElementById("movers-modal-close");
    var goBtn = document.getElementById("movers-modal-go");
    if (closeBtn) closeBtn.addEventListener("click", closeModal);
    if (goBtn) goBtn.addEventListener("click", closeModal);
    modal.addEventListener("click", function (evt) {
      if (evt.target === modal) closeModal();
    });
    document.addEventListener("keydown", function (evt) {
      if (evt.key === "Escape" && !modal.hidden) closeModal();
    });
    var aboutBtn = document.getElementById("movers-about-btn");
    if (aboutBtn) aboutBtn.addEventListener("click", openModal);
  }

  render();
})();
