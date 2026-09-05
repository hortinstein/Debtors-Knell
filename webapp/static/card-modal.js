// Site-wide card modal.
//
// Every card name on the site is a link to that card's page
// (/card/<slug>/, see webapp/app.py's card_detail). This script turns those
// links into an in-place modal -- the card's picture, its price history and
// every decklist in the archive that runs it -- so you can look a card up
// from the middle of a decklist without losing your place. The link itself
// still works: middle-click, ctrl/cmd-click, a shared URL, or JavaScript
// being off all land on the full page instead.
//
// The Scryfall hover preview (card-preview.js) is untouched and still fires
// over these links; this only takes over the click.
(function () {
  "use strict";

  // Card pages live at <site>/card/<slug>/ and their JSON at
  // <site>/card/<slug>.json. This file is served from <site>/static/, which
  // is what makes "../card/" the right base from its own URL, whether the
  // site is at a domain root or under a GitHub Pages project subpath.
  var script = document.currentScript;
  var CARD_BASE = script ? new URL("../card/", script.src).href : "/card/";
  var CARD_PATH_RE = /\/card\/([^/?#]+)\/?$/;

  // Mirrors card_slug() in webapp/app.py, so a script rendering card names
  // client-side (movers.js, pool.js) can link them without a lookup table.
  function cardSlug(name) {
    return String(name)
      .replace(/Æ/g, "AE").replace(/æ/g, "ae")
      .normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
      .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "card";
  }

  function cardUrl(name) {
    return CARD_BASE + cardSlug(name) + "/";
  }

  // Used by the client-rendered card lists (the pool builder's shopping list,
  // the movers rankings) to build the same link this script opens.
  window.cardLinks = { slug: cardSlug, url: cardUrl };

  var cache = {};
  var backdrop = null;
  var lastFocused = null;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function money(v, unit) {
    if (v == null) return "N/A";
    return unit === "tix" ? v.toFixed(2) + " tix" : "$" + v.toFixed(2);
  }

  function ensureBackdrop() {
    if (backdrop) return backdrop;
    backdrop = el("div", "card-modal-backdrop");
    backdrop.hidden = true;
    backdrop.addEventListener("click", function (evt) {
      if (evt.target === backdrop) close();
    });
    document.body.appendChild(backdrop);
    return backdrop;
  }

  function close() {
    if (!backdrop || backdrop.hidden) return;
    backdrop.hidden = true;
    backdrop.innerHTML = "";
    document.body.classList.remove("card-modal-open");
    if (lastFocused && lastFocused.focus) lastFocused.focus();
    lastFocused = null;
  }

  document.addEventListener("keydown", function (evt) {
    if (evt.key === "Escape") close();
  });

  function renderChart(card) {
    var wrap = el("div", "price-chart");
    wrap.dataset.subject = "card";
    // "</" inside the JSON would end the <script> tag early; nothing else in
    // a series of dates and numbers can break out of it.
    var payload = JSON.stringify(card.history || { tix: [], usd: [] }).replace(/</g, "\\u003c");
    wrap.innerHTML =
      '<div class="price-chart-header">' +
      '<span class="price-chart-title">Price history</span>' +
      '<div class="price-chart-toggle" role="group" aria-label="Currency">' +
      '<button type="button" class="toggle-btn active" data-series="tix">Digital (tix)</button>' +
      '<button type="button" class="toggle-btn" data-series="usd">Physical ($)</button>' +
      "</div></div>" +
      '<div class="price-chart-canvas-wrap">' +
      '<canvas class="price-chart-canvas"></canvas>' +
      '<div class="price-chart-tooltip"><div class="pc-tt-value"></div><div class="pc-tt-date"></div></div>' +
      "</div>" +
      '<p class="price-chart-note"></p>' +
      '<script type="application/json" class="price-chart-data">' + payload + "<\/script>";
    return wrap;
  }

  function renderDecks(card, jsonUrl) {
    var section = el("div", "card-modal-decks");
    section.appendChild(el("h3", null,
      "In " + card.decks.length + " decklist" + (card.decks.length !== 1 ? "s" : "")));
    var list = el("ul", "card-modal-deck-list");
    card.decks.forEach(function (d) {
      var li = el("li");
      var a = el("a", null, d.title);
      // Deck URLs come out of the JSON relative to the JSON's own location,
      // which is not where the page showing this modal lives.
      a.href = new URL(d.url, jsonUrl).href;
      li.appendChild(a);
      if (d.label) li.appendChild(el("span", "card-modal-deck-label", " " + d.label));
      li.appendChild(el("span", "card-modal-deck-meta",
        " · " + d.date + " · " + d.role_label + " · ×" + d.qty));
      list.appendChild(li);
    });
    section.appendChild(list);
    return section;
  }

  function renderModal(card, pageUrl, jsonUrl) {
    var modal = el("div", "card-modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-label", card.name);

    var closeBtn = el("button", "card-modal-close", "×");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close");
    closeBtn.addEventListener("click", close);
    modal.appendChild(closeBtn);

    var img = document.createElement("img");
    img.className = "card-modal-image";
    img.alt = card.name;
    img.src = card.image;
    modal.appendChild(img);

    var body = el("div", "card-modal-body");
    body.appendChild(el("h2", "card-modal-title", card.name));

    var prices = el("p", "card-modal-prices");
    prices.appendChild(el("span", null, money(card.unit_usd, "usd") + " physical"));
    prices.appendChild(el("span", null, money(card.unit_tix, "tix") + " digital"));
    body.appendChild(prices);

    body.appendChild(el("p", "card-modal-counts",
      "In " + card.num_decks + " decklist" + (card.num_decks !== 1 ? "s" : "") +
      " (" + card.num_budget_decks + " budget build" + (card.num_budget_decks !== 1 ? "s" : "") +
      "), " + card.total_qty + " cop" + (card.total_qty !== 1 ? "ies" : "y") + " in total."));

    body.appendChild(renderChart(card));
    body.appendChild(renderDecks(card, jsonUrl));

    var links = el("p", "card-modal-links");
    var page = el("a", null, "Open card page →");
    page.href = pageUrl;
    links.appendChild(page);
    if (card.scryfall) {
      var sf = el("a", null, "View on Scryfall →");
      sf.href = card.scryfall;
      sf.target = "_blank";
      sf.rel = "noopener";
      links.appendChild(sf);
    }
    body.appendChild(links);

    modal.appendChild(body);
    return modal;
  }

  function missingModal(pageUrl) {
    var modal = el("div", "card-modal");
    var closeBtn = el("button", "card-modal-close", "×");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close");
    closeBtn.addEventListener("click", close);
    modal.appendChild(closeBtn);
    var body = el("div", "card-modal-body");
    var name = decodeURIComponent((pageUrl.match(CARD_PATH_RE) || [])[1] || "this card")
      .replace(/-/g, " ");
    body.appendChild(el("h2", "card-modal-title", "No card page for " + name));
    body.appendChild(el("p", "card-modal-counts",
      "No decklist in the archive names this card, so there is nothing to show for it here."));
    modal.appendChild(body);
    return modal;
  }

  function show(node) {
    var bd = ensureBackdrop();
    bd.innerHTML = "";
    bd.appendChild(node);
    bd.hidden = false;
    document.body.classList.add("card-modal-open");
  }

  function open(pageUrl, name) {
    var jsonUrl = pageUrl.replace(/\/$/, "") + ".json";
    lastFocused = document.activeElement;
    // The link carries the card's name (data-card), so the modal can say what
    // it is opening while the fetch is in flight.
    show(el("div", "card-modal card-modal-loading", "Loading " + (name || "card") + "…"));

    var render = function (card) {
      var modal = renderModal(card, pageUrl, jsonUrl);
      show(modal);
      if (window.initPriceChart) window.initPriceChart(modal.querySelector(".price-chart"));
      var closeBtn = modal.querySelector(".card-modal-close");
      if (closeBtn) closeBtn.focus();
    };

    if (cache[jsonUrl]) {
      render(cache[jsonUrl]);
      return;
    }
    fetch(jsonUrl)
      .then(function (r) {
        if (r.status === 404) return null;
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (card) {
        if (!card) {
          // No card page for this name. A page built before a rename can
          // still be showing one (an archived movers day, say), and sending
          // the reader to a 404 would be worse than saying so here.
          show(missingModal(pageUrl));
          return;
        }
        cache[jsonUrl] = card;
        render(card);
      })
      .catch(function () {
        // Something other than a missing card -- the link's own page is the
        // better place to fail, rather than an empty modal.
        close();
        window.location.href = pageUrl;
      });
  }

  document.addEventListener("click", function (evt) {
    // Let the browser handle every click that means "somewhere else": a new
    // tab, a new window, a download, a save.
    if (evt.defaultPrevented || evt.button !== 0) return;
    if (evt.metaKey || evt.ctrlKey || evt.shiftKey || evt.altKey) return;
    var link = evt.target.closest ? evt.target.closest("a[href]") : null;
    if (!link || link.target === "_blank") return;
    var url = new URL(link.href, window.location.href);
    // Same-origin only: an article body could well link somewhere external
    // whose path happens to end in /card/<something>/.
    if (url.origin !== window.location.origin) return;
    if (!CARD_PATH_RE.test(url.pathname)) return;
    evt.preventDefault();
    open(link.href, link.dataset.card || link.textContent.trim());
  });
})();
