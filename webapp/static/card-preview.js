// Site-wide card-image hover preview for Scryfall card links.
//
// Whenever the cursor is over an <a> whose href matches
// scryfall.com/card/<set>/<number>/<slug> -- or anywhere on a table row that
// contains one, which is how the priced decklists are laid out -- show a
// floating card image near the cursor by rewriting that URL directly into a
// Scryfall image API URL: no JSON fetch, no external libraries.
(function () {
  "use strict";

  var CARD_LINK_RE = /scryfall\.com\/card\/([^\/?#]+)\/([^\/?#]+)\//;

  var preview = document.createElement("img");
  preview.id = "card-preview";
  preview.alt = "";
  document.body.appendChild(preview);

  function ancestorCardLink(el) {
    while (el && el.nodeType === 1 && el !== document.body) {
      if (el.tagName === "A" && el.href && CARD_LINK_RE.test(el.href)) {
        return el;
      }
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
      var rowLink = row.querySelector("a[href]");
      if (rowLink && CARD_LINK_RE.test(rowLink.href)) {
        return { zone: row, link: rowLink };
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
    var m = target.link.href.match(CARD_LINK_RE);
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
