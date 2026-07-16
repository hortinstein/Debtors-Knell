// Click-to-expand lightbox for the archived page screenshot on deck pages.
(function () {
  "use strict";

  var thumb = document.querySelector(".screenshot-thumb");
  if (!thumb) return;

  var overlay = document.createElement("div");
  overlay.id = "lightbox-overlay";
  overlay.innerHTML =
    '<button type="button" id="lightbox-close" aria-label="Close">&times;</button>' +
    '<img id="lightbox-img" alt="">';
  document.body.appendChild(overlay);

  var img = overlay.querySelector("#lightbox-img");
  var closeBtn = overlay.querySelector("#lightbox-close");

  function open() {
    img.src = thumb.dataset.full;
    img.alt = thumb.alt;
    overlay.classList.add("open");
    document.body.classList.add("lightbox-locked");
  }

  function close() {
    overlay.classList.remove("open");
    document.body.classList.remove("lightbox-locked");
    img.removeAttribute("src");
  }

  thumb.addEventListener("click", open);
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", function (evt) {
    if (evt.target === overlay) close();
  });
  document.addEventListener("keydown", function (evt) {
    if (evt.key === "Escape" && overlay.classList.contains("open")) close();
  });
})();
