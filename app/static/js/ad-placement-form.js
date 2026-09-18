// Keeps the Placement form's Section/Article fields, mobile-fallback
// field, and preview panel in sync with whatever slot + scope is
// currently selected: only shows the fields relevant to the chosen
// scope/slot, and only offers section options actually valid for the
// chosen slot (matching the server-side validation in app.py's
// placement_new(), so nothing here is authoritative -- it's UX only).
(function () {
  var slotSelect = document.getElementById("slot");
  var scopeSelect = document.getElementById("scope");
  var sectionSelect = document.getElementById("section");
  var sectionRow = document.getElementById("section-row");
  var contentRow = document.getElementById("content-row");
  var fallbackRow = document.getElementById("mobile-fallback-row");
  var previewBody = document.getElementById("placement-preview-body");
  var dataEl = document.getElementById("ad-slots-data");
  if (!slotSelect || !scopeSelect || !sectionSelect) return;

  var SLOTS = {};
  if (dataEl) {
    try { SLOTS = JSON.parse(dataEl.textContent) || {}; } catch (e) { SLOTS = {}; }
  }

  function selectedSlotOption() {
    return slotSelect.options[slotSelect.selectedIndex] || null;
  }

  function applySlotConstraints() {
    var opt = selectedSlotOption();
    var contexts = (opt && opt.dataset.contexts ? opt.dataset.contexts.split(",") : []);
    var supportsContent = !!(opt && opt.dataset.supportsContent === "true");

    var sectionHasValidSelection = false;
    Array.prototype.forEach.call(sectionSelect.options, function (o) {
      var valid = contexts.indexOf(o.value) !== -1;
      o.hidden = !valid;
      o.disabled = !valid;
      if (valid && o.selected) sectionHasValidSelection = true;
    });
    if (!sectionHasValidSelection) {
      for (var i = 0; i < sectionSelect.options.length; i++) {
        if (!sectionSelect.options[i].disabled) {
          sectionSelect.value = sectionSelect.options[i].value;
          break;
        }
      }
    }

    var contentOption = scopeSelect.querySelector('option[value="content"]');
    if (contentOption) {
      contentOption.disabled = !supportsContent;
      if (!supportsContent && scopeSelect.value === "content") {
        scopeSelect.value = "global";
      }
    }

    applyScopeVisibility();
    applyFallbackVisibility();
    renderPreview();
  }

  function applyScopeVisibility() {
    var scope = scopeSelect.value;
    if (sectionRow) sectionRow.hidden = scope !== "section";
    if (contentRow) contentRow.hidden = scope !== "content";
  }

  function currentSlotMeta() {
    var opt = selectedSlotOption();
    return opt ? SLOTS[opt.value] : null;
  }

  function applyFallbackVisibility() {
    if (!fallbackRow) return;
    var meta = currentSlotMeta();
    var hasFallback = !!(meta && meta.mobile_fallback_choices && meta.mobile_fallback_choices.length);
    fallbackRow.hidden = !hasFallback;
  }

  // A small, purely illustrative page diagram -- highlights roughly
  // where on the page the selected slot sits, using the slot's own key
  // (not a hardcoded per-slot lookup) so a newly added slot still gets a
  // sensible diagram automatically as long as its name follows the
  // existing top/sidebar/bottom/after_* convention.
  function diagramZone(slotKey) {
    if (/sidebar/.test(slotKey)) return "sidebar";
    if (/bottom/.test(slotKey)) return "bottom";
    if (/top/.test(slotKey)) return "top";
    if (/after_/.test(slotKey)) return "inline";
    return "top";
  }

  function renderDiagram(slotKey) {
    var zone = diagramZone(slotKey);
    var hasSidebar = zone === "sidebar";
    return (
      '<div class="placement-diagram" aria-hidden="true">' +
        '<div class="placement-diagram-page' + (hasSidebar ? ' has-sidebar' : '') + '">' +
          '<div class="pd-zone pd-top' + (zone === "top" ? " pd-active" : "") + '"></div>' +
          '<div class="pd-main">' +
            '<div class="pd-zone pd-line"></div>' +
            '<div class="pd-zone pd-line short"></div>' +
            (zone === "inline" ? '<div class="pd-zone pd-inline pd-active"></div>' : '') +
            '<div class="pd-zone pd-line"></div>' +
            '<div class="pd-zone pd-line short"></div>' +
            '<div class="pd-zone pd-bottom' + (zone === "bottom" ? " pd-active" : "") + '"></div>' +
          '</div>' +
          (hasSidebar ? '<div class="pd-zone pd-sidebar pd-active"></div>' : '') +
        '</div>' +
      '</div>'
    );
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function renderPreview() {
    if (!previewBody) return;
    var opt = selectedSlotOption();
    var meta = currentSlotMeta();
    if (!opt || !opt.value || !meta) {
      previewBody.innerHTML = '<p class="hint">Önizlemeyi görmek için yukarıdan bir slot seçin.</p>';
      return;
    }
    previewBody.innerHTML =
      renderDiagram(opt.value) +
      '<p style="margin:14px 0 4px; font-weight:700;">' + esc(meta.label) + '</p>' +
      '<p class="hint" style="margin:0 0 10px;">' + esc(meta.description || "") + '</p>' +
      '<dl class="placement-preview-facts">' +
        '<dt>En-boy oranı</dt><dd>' + esc(meta.aspect_ratio || "—") + '</dd>' +
        '<dt>Masaüstü</dt><dd>' + esc(meta.desktop_behavior || "—") + '</dd>' +
        '<dt>Tablet</dt><dd>' + esc(meta.tablet_behavior || "—") + '</dd>' +
        '<dt>Mobil</dt><dd>' + esc(meta.mobile_behavior || "—") + '</dd>' +
      '</dl>';
  }

  slotSelect.addEventListener("change", applySlotConstraints);
  scopeSelect.addEventListener("change", applyScopeVisibility);
  applySlotConstraints();
})();
