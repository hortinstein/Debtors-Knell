// Per-deck historical price chart, digital (tix) / physical (USD) toggle.
// Vanilla canvas line chart, no external dependencies. Data comes from a
// sibling <script type="application/json"> tag holding
// {tix: [[date, value], ...], usd: [[date, value], ...], unmatched_cards: [...]}.
(function () {
  "use strict";

  var blocks = document.querySelectorAll(".price-chart");
  if (!blocks.length) return;

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function formatDate(d) {
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }

  function formatMoney(v, unit) {
    if (unit === "tix") return v.toFixed(2) + " tix";
    return "$" + v.toFixed(2);
  }

  function initChart(block) {
    var dataEl = block.querySelector(".price-chart-data");
    var canvas = block.querySelector(".price-chart-canvas");
    var noteEl = block.querySelector(".price-chart-note");
    var toggleBtns = block.querySelectorAll(".toggle-btn");
    if (!dataEl || !canvas) return;

    var raw;
    try {
      raw = JSON.parse(dataEl.textContent);
    } catch (e) {
      return;
    }

    var series = { tix: raw.tix || [], usd: raw.usd || [] };
    var current = "tix";
    var ctx = canvas.getContext("2d");
    var tooltip = block.querySelector(".price-chart-tooltip");

    function points(key) {
      return series[key].map(function (p) {
        return { date: new Date(p[0] + "T00:00:00Z"), value: p[1] };
      });
    }

    function draw() {
      var pts = points(current);
      var dpr = window.devicePixelRatio || 1;
      var cssWidth = canvas.clientWidth || 720;
      var cssHeight = 220;
      canvas.width = cssWidth * dpr;
      canvas.height = cssHeight * dpr;
      canvas.style.height = cssHeight + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cssWidth, cssHeight);

      var border = cssVar("--border") || "#ddd6c9";
      var muted = cssVar("--muted") || "#6b6b6b";
      var accent = cssVar("--accent-2") || "#9c5518";
      var fg = cssVar("--fg") || "#232323";

      if (!pts.length) {
        ctx.fillStyle = muted;
        ctx.font = "13px sans-serif";
        ctx.fillText("No price history available for this deck.", 8, cssHeight / 2);
        if (noteEl) noteEl.textContent = "";
        return;
      }

      var padL = 54, padR = 14, padT = 14, padB = 26;
      var plotW = cssWidth - padL - padR;
      var plotH = cssHeight - padT - padB;

      if (pts.length === 1) {
        // Single data point: no meaningful line, draw a marker instead.
        var p = pts[0];
        var cx = padL + plotW / 2;
        var cy = padT + plotH / 2;
        ctx.strokeStyle = border;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padL, cy + 0.5);
        ctx.lineTo(padL + plotW, cy + 0.5);
        ctx.stroke();
        ctx.fillStyle = accent;
        ctx.beginPath();
        ctx.arc(cx, cy, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = fg;
        ctx.font = "13px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(formatMoney(p.value, current) + " (" + formatDate(p.date) + ")", cx, cy - 14);
        ctx.textAlign = "start";
        if (noteEl) {
          noteEl.textContent = current === "usd"
            ? "Only one day of physical price history has been archived so far (archiving started 2026-07-16) — this chart will fill in as more days accumulate."
            : "Only one day of digital price history matched.";
        }
        return;
      }

      var minX = pts[0].date.getTime();
      var maxX = pts[pts.length - 1].date.getTime();
      var values = pts.map(function (p) { return p.value; });
      var maxY = Math.max.apply(null, values) * 1.12 || 1;
      var minY = 0;

      function xPix(t) {
        return padL + ((t - minX) / (maxX - minX || 1)) * plotW;
      }
      function yPix(v) {
        return padT + plotH - ((v - minY) / (maxY - minY || 1)) * plotH;
      }

      // gridlines + y-axis labels (recessive)
      ctx.strokeStyle = border;
      ctx.fillStyle = muted;
      ctx.font = "11px sans-serif";
      ctx.lineWidth = 1;
      var yTicks = 4;
      for (var i = 0; i <= yTicks; i++) {
        var v = (maxY / yTicks) * i;
        var y = yPix(v);
        ctx.globalAlpha = 0.6;
        ctx.beginPath();
        ctx.moveTo(padL, y + 0.5);
        ctx.lineTo(padL + plotW, y + 0.5);
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.textAlign = "right";
        ctx.fillText(v.toFixed(v < 10 ? 2 : 0), padL - 6, y + 3);
      }

      // x-axis labels: first, middle, last date
      ctx.textAlign = "center";
      [0, Math.floor(pts.length / 2), pts.length - 1].forEach(function (idx) {
        var pt = pts[idx];
        ctx.fillText(formatDate(pt.date), xPix(pt.date.getTime()), cssHeight - 6);
      });
      ctx.textAlign = "start";

      // line
      ctx.strokeStyle = accent;
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.beginPath();
      pts.forEach(function (p, idx) {
        var x = xPix(p.date.getTime());
        var y = yPix(p.value);
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();

      if (noteEl) noteEl.textContent = "";

      // hover crosshair + tooltip
      canvas.onmousemove = function (evt) {
        var rect = canvas.getBoundingClientRect();
        var mx = evt.clientX - rect.left;
        var t = minX + ((mx - padL) / plotW) * (maxX - minX);
        // nearest point by time
        var nearest = pts[0], best = Infinity;
        for (var j = 0; j < pts.length; j++) {
          var d = Math.abs(pts[j].date.getTime() - t);
          if (d < best) { best = d; nearest = pts[j]; }
        }
        draw();
        var nx = xPix(nearest.date.getTime());
        var ny = yPix(nearest.value);
        ctx.strokeStyle = muted;
        ctx.globalAlpha = 0.7;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(nx + 0.5, padT);
        ctx.lineTo(nx + 0.5, padT + plotH);
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.fillStyle = accent;
        ctx.beginPath();
        ctx.arc(nx, ny, 3.5, 0, Math.PI * 2);
        ctx.fill();

        if (tooltip) {
          tooltip.style.display = "block";
          var valueEl = tooltip.querySelector(".pc-tt-value");
          var dateEl = tooltip.querySelector(".pc-tt-date");
          if (valueEl) valueEl.textContent = formatMoney(nearest.value, current);
          if (dateEl) dateEl.textContent = formatDate(nearest.date);
          var left = Math.min(Math.max(nx - 40, 0), cssWidth - 110);
          tooltip.style.left = left + "px";
          tooltip.style.top = "2px";
        }
      };
      canvas.onmouseleave = function () {
        if (tooltip) tooltip.style.display = "none";
        draw();
      };
    }

    toggleBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        current = btn.getAttribute("data-series");
        toggleBtns.forEach(function (b) { b.classList.toggle("active", b === btn); });
        draw();
      });
    });

    window.addEventListener("resize", draw);
    if (window.matchMedia) {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
    }
    draw();
  }

  blocks.forEach(initChart);
})();
