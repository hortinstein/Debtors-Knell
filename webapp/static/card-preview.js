// Site-wide card-image hover preview for Scryfall card links.
//
// Whenever the cursor is over an element naming a Scryfall printing --
// an <a> whose href matches scryfall.com/card/<set>/<number>/<slug>, a card
// link carrying one in data-scryfall, or anywhere on a table row that
// contains either, which is how the priced decklists are laid out -- show a
// floating card image near the cursor by rewriting that URL directly into a
// Scryfall image API URL: no JSON fetch, no external libraries.
(function () {
  "use strict";

  var CARD_LINK_RE = /scryfall\.com\/card\/([^\/?#]+)\/([^\/?#]+)\//;

  var preview = document.createElement("img");
  preview.id = "card-preview";
  preview.alt = "";
  document.body.appendChild(preview);

  // The Scryfall card URL an element stands for: its own href, or -- for the
  // card links card-modal.js opens (whose href is the card's page on this
  // site) -- the printing recorded in data-scryfall. That attribute is what
  // keeps the hover preview working on pages like Card Stats, where a card
  // name is the only thing on the row.
  function scryfallUrlFor(el) {
    if (el.dataset && el.dataset.scryfall && CARD_LINK_RE.test(el.dataset.scryfall)) {
      return el.dataset.scryfall;
    }
    if (el.tagName === "A" && el.href && CARD_LINK_RE.test(el.href)) {
      return el.href;
    }
    return null;
  }

  function ancestorCardLink(el) {
    while (el && el.nodeType === 1 && el !== document.body) {
      if (scryfallUrlFor(el)) return el;
      el = el.parentElement;
    }
    return null;
  }

  // The hover target: the card link under the cursor, or -- in a decklist
  // table, where the Scryfall link sits alone in the last column -- the whole
  // row that link belongs to. Anywhere the cursor is over a row naming a
  // card, that card is what you want to see; having to find the little "link"
  // cell first was needless precision.
  //
  // Returns the element the preview should stay open over, plus the link the
  // image comes from, so mouseout can tell when the cursor has really left.
  function hoverTarget(el) {
    if (!el || el.nodeType !== 1) return null;
    var link = ancestorCardLink(el);
    if (link) return { zone: link, link: link };
    var row = el.closest ? el.closest("tr") : null;
    if (row) {
      // Every link in the row, not just the first: since the Card column
      // became a link to the card's own page, the row's first link is no
      // longer the Scryfall one.
      var candidates = row.querySelectorAll("a[href], [data-scryfall]");
      for (var i = 0; i < candidates.length; i++) {
        if (scryfallUrlFor(candidates[i])) {
          return { zone: row, link: candidates[i] };
        }
      }
    }
    return null;
  }

  function positionPreview(x, y) {
    var margin = 18;
    var w = preview.offsetWidth || 244;
    var h = preview.offsetHeight || 340;
    var left = x + margin;
    var top = y + margin;
    if (left + w > window.innerWidth) left = Math.max(0, x - margin - w);
    if (top + h > window.innerHeight) top = Math.max(0, window.innerHeight - h - margin);
    preview.style.left = left + "px";
    preview.style.top = top + "px";
  }

  var activeZone = null;

  document.addEventListener("mouseover", function (evt) {
    var target = hoverTarget(evt.target);
    if (!target) return;
    var url = scryfallUrlFor(target.link);
    if (!url) return;
    var m = url.match(CARD_LINK_RE);
    if (!m) return;
    activeZone = target.zone;
    var set = m[1];
    var number = m[2];
    var imgUrl = "https://api.scryfall.com/cards/" + encodeURIComponent(set) +
      "/" + encodeURIComponent(number) + "?format=image&version=normal";
    if (preview.src !== imgUrl) {
      preview.src = imgUrl;
    }
    preview.style.display = "block";
    positionPreview(evt.clientX, evt.clientY);
  });

  document.addEventListener("mousemove", function (evt) {
    if (preview.style.display === "block") {
      positionPreview(evt.clientX, evt.clientY);
    }
  });

  document.addEventListener("mouseout", function (evt) {
    if (!activeZone) return;
    // Moving between cells of the same row must not flicker the preview, so
    // hide only once the cursor has left the whole zone.
    if (evt.relatedTarget && activeZone.contains(evt.relatedTarget)) return;
    activeZone = null;
    preview.style.display = "none";
    preview.removeAttribute("src");
  });
})();
