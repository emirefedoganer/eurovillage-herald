// Keeps the Placement form's Section/Article fields in sync with whatever
// slot + scope is currently selected: only shows the fields relevant to the
// chosen scope, and only offers section options actually valid for the
// chosen slot (matching the server-side validation in app.py's
// placement_new(), so nothing here is authoritative -- it's UX only).
(function () {
  var slotSelect = document.getElementById("slot");
  var scopeSelect = document.getElementById("scope");
  var sectionSelect = document.getElementById("section");
  var sectionRow = document.getElementById("section-row");
  var contentRow = document.getElementById("content-row");
  if (!slotSelect || !scopeSelect || !sectionSelect) return;

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
  }

  function applyScopeVisibility() {
    var scope = scopeSelect.value;
    if (sectionRow) sectionRow.hidden = scope !== "section";
    if (contentRow) contentRow.hidden = scope !== "content";
  }

  slotSelect.addEventListener("change", applySlotConstraints);
  scopeSelect.addEventListener("change", applyScopeVisibility);
  applySlotConstraints();
})();
