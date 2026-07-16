// Site-wide card-image hover preview for Scryfall card links.
//
// Whenever the cursor is over an <a> whose href matches
// scryfall.com/card/<set>/<number>/<slug>, show a floating card image near
// the cursor by rewriting that URL directly into a Scryfall image API URL --
// no JSON fetch, no external libraries.
(function () {
  "use strict";

  var CARD_LINK_RE = /scryfall\.com\/card\/([^\/?#]+)\/([^\/?#]+)\//;

  var preview = document.createElement("img");
  preview.id = "card-preview";
  preview.alt = "";
  document.body.appendChild(preview);

  function findCardLink(el) {
    while (el && el.nodeType === 1 && el !== document.body) {
      if (el.tagName === "A" && el.href && CARD_LINK_RE.test(el.href)) {
        return el;
      }
      el = el.parentElement;
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

  document.addEventListener("mouseover", function (evt) {
    var link = findCardLink(evt.target);
    if (!link) return;
    var m = link.href.match(CARD_LINK_RE);
    if (!m) return;
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
    var link = findCardLink(evt.target);
    if (!link) return;
    if (evt.relatedTarget && link.contains(evt.relatedTarget)) return;
    preview.style.display = "none";
    preview.removeAttribute("src");
  });
})();
