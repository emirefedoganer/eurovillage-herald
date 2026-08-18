/*
 * Small UX helper for the manual admin sudoku grid (81 plain <input> cells):
 * typing a digit auto-advances, arrow keys move between cells, and only
 * 1-9 (or empty) is ever accepted. The actual validation happens server-side
 * on save.
 */
(function () {
  "use strict";
  const grid = document.querySelector(".skb-grid");
  if (!grid) return;
  const inputs = Array.from(grid.querySelectorAll("input"));

  inputs.forEach((input, idx) => {
    input.addEventListener("input", () => {
      input.value = input.value.replace(/[^1-9]/g, "").slice(0, 1);
      if (input.value && idx + 1 < inputs.length) inputs[idx + 1].focus();
    });
    input.addEventListener("keydown", (e) => {
      const r = Math.floor(idx / 9), c = idx % 9;
      const moves = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 9, ArrowUp: -9 };
      if (e.key in moves) {
        e.preventDefault();
        const next = idx + moves[e.key];
        if (next >= 0 && next < inputs.length) inputs[next].focus();
      } else if (e.key === "Backspace" && !input.value) {
        if (idx - 1 >= 0) { e.preventDefault(); inputs[idx - 1].focus(); }
      }
    });
  });
})();
