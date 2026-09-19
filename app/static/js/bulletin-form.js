(function () {
  "use strict";
  var kind = document.getElementById("kind");
  var audience = document.getElementById("target_preference");
  if (!kind || !audience) return;

  var map = JSON.parse(document.getElementById("audience-map").textContent);
  var labels = JSON.parse(document.getElementById("audience-labels").textContent);
  var countEl = document.getElementById("audience-count");
  var popularPanel = document.getElementById("popular-panel");
  var generic = document.getElementById("generic-articles");
  var issueRow = document.getElementById("issue-row");
  var refreshed = document.getElementById("analytics_refreshed");
  var list = document.getElementById("popular-list");
  var lastPopularPulled = false;

  function setDisabled(container, disabled) {
    container.querySelectorAll("input[name='article_slugs'], input[name='analytics_refreshed']").forEach(function (el) {
      el.disabled = disabled;
    });
  }

  function updateEstimate() {
    fetch(kind.getAttribute("data-audience-url") + "?kind=" + encodeURIComponent(kind.value) + "&audience=" + encodeURIComponent(audience.value),
          { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) { countEl.textContent = d.eligible; });
  }

  function applyKind() {
    var spec = map[kind.value];
    var previous = audience.value;
    // never keep an audience the new type does not allow: rebuild the options,
    // then pick the type's default unless a custom type keeps a still-valid one
    audience.innerHTML = "";
    spec.allowed.forEach(function (key) {
      var o = document.createElement("option");
      o.value = key; o.textContent = labels[key];
      audience.appendChild(o);
    });
    audience.value = (spec.custom && spec.allowed.indexOf(previous) >= 0) ? previous : spec["default"];
    audience.disabled = false;
    var popular = kind.value === "popular_stories";
    popularPanel.hidden = !popular;
    generic.hidden = popular;
    setDisabled(popularPanel, !popular);
    setDisabled(generic, popular);
    if (issueRow) issueRow.hidden = kind.value !== "new_issue";
    if (popular && list && !list.children.length) pullRanking();
    updateEstimate();
  }

  function render(items) {
    list.innerHTML = "";
    items.forEach(function (it) {
      var li = document.createElement("li");
      li.className = "popular-item";
      li.style.cssText = "display:flex; align-items:center; gap:8px; padding:6px 0; border-bottom:1px solid var(--gray-100);";
      var hidden = document.createElement("input");
      hidden.type = "hidden"; hidden.name = "article_slugs"; hidden.value = it.slug;
      var span = document.createElement("span");
      span.style.flex = "1";
      span.textContent = it.title + " ";
      var hint = document.createElement("span");
      hint.className = "hint";
      hint.textContent = "— " + it.views + " görüntülenme";
      span.appendChild(hint);
      li.appendChild(hidden); li.appendChild(span);
      [["-1", "↑", "Yukarı taşı"], ["1", "↓", "Aşağı taşı"]].forEach(function (b) {
        var btn = document.createElement("button");
        btn.type = "button"; btn.className = "btn btn-outline"; btn.textContent = b[1];
        btn.setAttribute("data-move", b[0]); btn.setAttribute("aria-label", b[2]);
        li.appendChild(btn);
      });
      var rm = document.createElement("button");
      rm.type = "button"; rm.className = "btn btn-outline"; rm.textContent = "Çıkar";
      rm.setAttribute("data-remove", ""); rm.setAttribute("aria-label", "Listeden çıkar");
      li.appendChild(rm);
      list.appendChild(li);
    });
  }

  function pullRanking() {
    return fetch(kind.getAttribute("data-popular-url"), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        render(d.items);
        var w = document.getElementById("popular-warning");
        w.hidden = !d.warning; w.textContent = d.warning || "";
        if (refreshed) refreshed.value = "1";
      });
  }

  if (list) {
    list.addEventListener("click", function (e) {
      var li = e.target.closest("li");
      if (!li) return;
      if (e.target.hasAttribute("data-remove")) li.remove();
      var move = e.target.getAttribute("data-move");
      if (move === "-1" && li.previousElementSibling) list.insertBefore(li, li.previousElementSibling);
      if (move === "1" && li.nextElementSibling) list.insertBefore(li.nextElementSibling, li);
    });
    var btn = document.getElementById("popular-refresh");
    if (btn) btn.addEventListener("click", pullRanking);
  }

  kind.addEventListener("change", applyKind);
  audience.addEventListener("change", updateEstimate);
})();
