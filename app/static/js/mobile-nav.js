(function () {
  "use strict";

  var hamburgerBtn = document.querySelector(".hamburger-btn");
  var drawer = document.getElementById("mobile-drawer");
  var overlay = document.getElementById("drawer-overlay");
  var closeBtn = document.querySelector(".drawer-close-btn");
  var searchBtn = document.querySelector(".mobile-search-btn");
  var searchBar = document.getElementById("mobile-search-bar");

  if (hamburgerBtn && drawer && overlay) {
    var openDrawer = function () {
      drawer.hidden = false;
      overlay.hidden = false;
      hamburgerBtn.setAttribute("aria-expanded", "true");
      hamburgerBtn.setAttribute("aria-label", "Menüyü Kapat");
      document.body.classList.add("drawer-open");
      (closeBtn || drawer).focus();
    };
    var closeDrawer = function () {
      drawer.hidden = true;
      overlay.hidden = true;
      hamburgerBtn.setAttribute("aria-expanded", "false");
      hamburgerBtn.setAttribute("aria-label", "Menüyü Aç");
      document.body.classList.remove("drawer-open");
      hamburgerBtn.focus();
    };

    hamburgerBtn.addEventListener("click", function () {
      if (drawer.hidden) openDrawer(); else closeDrawer();
    });
    if (closeBtn) closeBtn.addEventListener("click", closeDrawer);
    overlay.addEventListener("click", closeDrawer);
    drawer.addEventListener("click", function (e) {
      if (e.target.closest("a")) closeDrawer();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !drawer.hidden) closeDrawer();
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 900 && !drawer.hidden) closeDrawer();
    });
  }

  if (searchBtn && searchBar) {
    searchBtn.addEventListener("click", function () {
      var willShow = searchBar.hidden;
      searchBar.hidden = !willShow;
      searchBtn.setAttribute("aria-expanded", String(willShow));
      if (willShow) {
        var input = searchBar.querySelector("input[name='q']");
        if (input) input.focus();
      }
    });
  }
})();
