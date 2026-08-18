/*
 * Admin crossword builder: click a square, type a letter -- no manual JSON
 * editing. Numbering/clue-slot detection mirrors games_engine.compute_slots()
 * so the clue list updates live as the grid changes; the server always
 * recomputes authoritatively from the posted grid on save.
 */
(function () {
  "use strict";

  const root = document.getElementById("xwb-root");
  if (!root) return;

  const initial = JSON.parse(document.getElementById("xwb-data").textContent);
  const saveUrl = root.dataset.saveUrl;

  let width = initial.width, height = initial.height;
  let grid = initial.grid; // [[{block, solution}]]
  const clueTexts = {}; // "num_dir" -> clue text
  (initial.clues || []).forEach((cl) => { clueTexts[cl.number + "_" + cl.direction] = cl.clue; });

  const gridEl = document.getElementById("xwb-grid");
  const acrossEl = document.getElementById("xwb-across-editor");
  const downEl = document.getElementById("xwb-down-editor");
  const statusEl = document.getElementById("xwb-save-status");
  const blockBtn = document.getElementById("xwb-toggle-block");

  let selected = null; // [r, c]
  const cellEls = [];

  function isLetter(r, c) {
    return r >= 0 && r < height && c >= 0 && c < width && !grid[r][c].block;
  }

  function computeSlots() {
    const numbers = {};
    const slots = [];
    let counter = 0;
    for (let r = 0; r < height; r++) {
      for (let c = 0; c < width; c++) {
        if (!isLetter(r, c)) continue;
        const startsAcross = !isLetter(r, c - 1) && isLetter(r, c + 1);
        const startsDown = !isLetter(r - 1, c) && isLetter(r + 1, c);
        if (startsAcross || startsDown) {
          counter += 1;
          numbers[r + "," + c] = counter;
          if (startsAcross) {
            const cells = [];
            let cc = c;
            while (isLetter(r, cc)) { cells.push([r, cc]); cc++; }
            slots.push({ number: counter, direction: "across", row: r, col: c, length: cells.length, cells, answer: cells.map(([rr, cc2]) => grid[rr][cc2].solution || " ").join("") });
          }
          if (startsDown) {
            const cells = [];
            let rr = r;
            while (isLetter(rr, c)) { cells.push([rr, c]); rr++; }
            slots.push({ number: counter, direction: "down", row: r, col: c, length: cells.length, cells, answer: cells.map(([rr2, cc]) => grid[rr2][cc].solution || " ").join("") });
          }
        }
      }
    }
    return { numbers, slots };
  }

  function buildGridDom() {
    gridEl.innerHTML = "";
    gridEl.style.setProperty("--xw-cols", width);
    gridEl.style.setProperty("--xw-rows", height);
    cellEls.length = 0;
    for (let r = 0; r < height; r++) {
      cellEls.push([]);
      for (let c = 0; c < width; c++) {
        const cell = document.createElement("button");
        cell.type = "button";
        cell.className = "xwb-cell";
        cell.dataset.r = r; cell.dataset.c = c;
        cell.addEventListener("click", () => { selected = [r, c]; render(); cell.focus(); });
        gridEl.appendChild(cell);
        cellEls[r].push(cell);
      }
    }
  }

  function render() {
    const { numbers, slots } = computeSlots();

    for (let r = 0; r < height; r++) {
      for (let c = 0; c < width; c++) {
        const el = cellEls[r][c];
        const data = grid[r][c];
        el.classList.toggle("xwb-block", data.block);
        el.classList.toggle("xw-selected", !!selected && selected[0] === r && selected[1] === c);
        el.innerHTML = "";
        if (!data.block) {
          const num = numbers[r + "," + c];
          if (num) {
            const numEl = document.createElement("span");
            numEl.className = "xw-number";
            numEl.textContent = num;
            el.appendChild(numEl);
          }
          el.appendChild(document.createTextNode(data.solution || ""));
        }
      }
    }

    renderClueEditor(acrossEl, slots.filter((s) => s.direction === "across"));
    renderClueEditor(downEl, slots.filter((s) => s.direction === "down"));
    blockBtn.disabled = !selected;
    if (selected) {
      blockBtn.textContent = grid[selected[0]][selected[1]].block ? "Harf Karesi Yap" : "Blok Kare Yap";
    }
  }

  function renderClueEditor(container, slots) {
    container.innerHTML = "";
    slots.sort((a, b) => a.number - b.number).forEach((slot) => {
      const key = slot.number + "_" + slot.direction;
      const row = document.createElement("div");
      row.className = "clue-row";
      const numSpan = document.createElement("span");
      numSpan.className = "num";
      numSpan.textContent = slot.number;
      const input = document.createElement("input");
      input.type = "text";
      input.placeholder = `(${slot.length} harf) ipucu yazın`;
      input.value = clueTexts[key] || "";
      if (!input.value.trim()) input.classList.add("empty-clue");
      input.addEventListener("input", () => {
        clueTexts[key] = input.value;
        input.classList.toggle("empty-clue", !input.value.trim());
      });
      row.appendChild(numSpan);
      row.appendChild(input);
      const ans = document.createElement("span");
      ans.style.fontFamily = "monospace";
      ans.style.fontSize = "12px";
      ans.style.color = "#888";
      ans.textContent = slot.answer.includes(" ") ? "(eksik)" : slot.answer;
      row.appendChild(ans);
      container.appendChild(row);
    });
    if (!slots.length) {
      container.innerHTML = '<p class="hint">Henüz kelime yok.</p>';
    }
  }

  const LETTER_RE = /^[a-zA-ZçğıöşüÇĞİÖŞÜ]$/;

  gridEl.addEventListener("keydown", (e) => {
    if (!selected) return;
    const [r, c] = selected;
    if (LETTER_RE.test(e.key)) {
      e.preventDefault();
      grid[r][c] = { block: false, solution: e.key.toLocaleUpperCase("tr-TR") };
      if (c + 1 < width) selected = [r, c + 1];
      render();
      if (selected) cellEls[selected[0]][selected[1]].focus();
      return;
    }
    if (e.key === "Backspace" || e.key === "Delete") {
      e.preventDefault();
      grid[r][c] = { block: grid[r][c].block, solution: "" };
      render();
      return;
    }
    const moves = { ArrowRight: [0, 1], ArrowLeft: [0, -1], ArrowDown: [1, 0], ArrowUp: [-1, 0] };
    if (moves[e.key]) {
      e.preventDefault();
      const [dr, dc] = moves[e.key];
      const nr = Math.min(height - 1, Math.max(0, r + dr));
      const nc = Math.min(width - 1, Math.max(0, c + dc));
      selected = [nr, nc];
      render();
      cellEls[nr][nc].focus();
    }
  });

  blockBtn.addEventListener("click", () => {
    if (!selected) return;
    const [r, c] = selected;
    grid[r][c] = grid[r][c].block ? { block: false, solution: "" } : { block: true, solution: "" };
    render();
  });

  document.getElementById("xwb-save")?.addEventListener("click", async () => {
    const { slots } = computeSlots();
    const clues = slots.map((s) => ({ number: s.number, direction: s.direction, clue: clueTexts[s.number + "_" + s.direction] || "" }));
    statusEl.textContent = "Kaydediliyor…";
    try {
      const res = await fetch(saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width, height, grid, clues }),
      });
      const data = await res.json();
      if (!res.ok) { statusEl.textContent = "Hata: " + (data.error || "kaydedilemedi"); return; }
      let msg = `Kaydedildi ✓ ${data.slot_count} kelime.`;
      if (data.conflicts && data.conflicts.length) msg += ` ${data.conflicts.length} bağlantısız kare bulundu.`;
      if (data.empty_clues && data.empty_clues.length) msg += ` Eksik ipucu: ${data.empty_clues.join(", ")}.`;
      statusEl.textContent = msg;
    } catch (err) {
      statusEl.textContent = "Kaydedilemedi: bağlantı hatası.";
    }
  });

  document.getElementById("xwb-clear")?.addEventListener("click", () => {
    if (!confirm("Tüm ızgara temizlensin mi?")) return;
    grid = Array.from({ length: height }, () => Array.from({ length: width }, () => ({ block: false, solution: "" })));
    render();
  });

  buildGridDom();
  render();
})();
