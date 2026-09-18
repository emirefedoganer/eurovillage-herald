(function () {
  "use strict";

  // Progressive enhancement only: the admin nav's dropdown groups
  // (.admin-nav-group, built on native <details>/<summary>) already work
  // with zero JS -- keyboard toggle, screen readers, everything. This
  // just adds two conveniences on top: only one group open at a time,
  // and closing a group on click-outside or Escape. Safe to load on
  // every page (base.html loads it unconditionally) -- on a public page
  // with no .admin-nav-group elements, this is a no-op.
  var groups = document.querySelectorAll(".admin-nav-group");
  if (!groups.length) return;

  groups.forEach(function (group) {
    group.addEventListener("toggle", function () {
      if (!group.open) return;
      groups.forEach(function (other) {
        if (other !== group) other.open = false;
      });
    });
  });

  document.addEventListener("click", function (e) {
    if (e.target.closest(".admin-nav-group")) return;
    groups.forEach(function (group) { group.open = false; });
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var openGroup = document.querySelector(".admin-nav-group[open]");
    if (!openGroup) return;
    openGroup.open = false;
    openGroup.querySelector("summary").focus();
  });
})();
