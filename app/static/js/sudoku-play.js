/*
 * Interactive sudoku player. Keyboard handling is scoped to the sudoku grid
 * element only, so it never hijacks typing anywhere else on the page.
 */
(function () {
  "use strict";

  const root = document.getElementById("sudoku-root");
  if (!root) return;

  const payload = JSON.parse(document.getElementById("sudoku-data").textContent);
  const { slug, starting_grid: givens, settings } = payload;
  const preview = root.dataset.preview === "1";
  const checkUrl = root.dataset.checkUrl;
  const hintUrl = root.dataset.hintUrl;

  const gridEl = document.getElementById("sk-grid");
  const messageEl = document.getElementById("sk-message");
  const timerEl = document.getElementById("sk-timer");
  const mistakesEl = document.getElementById("sk-mistakes");
  const hintsEl = document.getElementById("sk-hints-left");
  const notesBtn = document.getElementById("sk-notes-toggle");

  const state = {
    grid: givens.map((row) => row.slice()),
    notes: Array.from({ length: 9 }, () => Array.from({ length: 9 }, () => [])),
    selected: null,
    notesMode: false,
    mistakes: 0,
    hintsUsed: 0,
    elapsed: 0,
    completed: false,
    paused: false,
  };
  const undoStack = [];
  const redoStack = [];

  function snapshot() {
    return {
      grid: state.grid.map((r) => r.slice()),
      notes: state.notes.map((row) => row.map((cell) => cell.slice())),
    };
  }
  function pushUndo() {
    undoStack.push(snapshot());
    if (undoStack.length > 100) undoStack.shift();
    redoStack.length = 0;
  }
  function applySnapshot(snap) {
    state.grid = snap.grid.map((r) => r.slice());
    state.notes = snap.notes.map((row) => row.map((cell) => cell.slice()));
    renderAll();
  }

  // ---- build grid DOM ----
  const cellEls = [];
  for (let r = 0; r < 9; r++) {
    cellEls.push([]);
    for (let c = 0; c < 9; c++) {
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "sk-cell";
      if (givens[r][c]) cell.classList.add("sk-given");
      if ((Math.floor(r / 3) + Math.floor(c / 3)) % 2 === 1) cell.classList.add("sk-box-b");
      cell.dataset.r = r; cell.dataset.c = c;
      cell.setAttribute("aria-label", "Satır " + (r + 1) + " sütun " + (c + 1));
      const val = document.createElement("span");
      val.className = "sk-value";
      cell.appendChild(val);
      const notes = document.createElement("div");
      notes.className = "sk-notes";
      for (let n = 1; n <= 9; n++) {
        const ns = document.createElement("span");
        ns.className = "sk-note";
        ns.dataset.n = n;
        notes.appendChild(ns);
      }
      cell.appendChild(notes);
      cell.addEventListener("click", () => selectCell(r, c));
      gridEl.appendChild(cell);
      cellEls[r].push(cell);
    }
  }

  function selectCell(r, c) {
    if (state.paused) return;
    state.selected = [r, c];
    renderHighlights();
  }

  function isGiven(r, c) { return !!givens[r][c]; }

  function renderCell(r, c) {
    const el = cellEls[r][c];
    const v = state.grid[r][c];
    el.querySelector(".sk-value").textContent = v || "";
    el.classList.toggle("sk-empty", !v);
    el.querySelectorAll(".sk-note").forEach((ns) => {
      const n = parseInt(ns.dataset.n, 10);
      ns.classList.toggle("active", !v && state.notes[r][c].includes(n));
    });
  }

  function renderAll() {
    for (let r = 0; r < 9; r++) for (let c = 0; c < 9; c++) renderCell(r, c);
    renderConflicts();
    renderHighlights();
    persist();
  }

  function renderHighlights() {
    for (let r = 0; r < 9; r++) {
      for (let c = 0; c < 9; c++) {
        cellEls[r][c].classList.remove("sk-selected", "sk-peer", "sk-same-number");
      }
    }
    if (!state.selected) return;
    const [sr, sc] = state.selected;
    const selVal = state.grid[sr][sc];
    const br = Math.floor(sr / 3) * 3, bc = Math.floor(sc / 3) * 3;
    for (let r = 0; r < 9; r++) {
      for (let c = 0; c < 9; c++) {
        const inBox = r >= br && r < br + 3 && c >= bc && c < bc + 3;
        if (r === sr || c === sc || inBox) cellEls[r][c].classList.add("sk-peer");
        if (selVal && state.grid[r][c] === selVal) cellEls[r][c].classList.add("sk-same-number");
      }
    }
    cellEls[sr][sc].classList.add("sk-selected");
  }

  function renderConflicts() {
    const conflicts = new Set();
    function checkGroup(cells) {
      const seen = {};
      cells.forEach(([r, c]) => {
        const v = state.grid[r][c];
        if (!v) return;
        (seen[v] = seen[v] || []).push([r, c]);
      });
      Object.values(seen).forEach((list) => {
        if (list.length > 1) list.forEach(([r, c]) => conflicts.add(r + "," + c));
      });
    }
    for (let i = 0; i < 9; i++) {
      checkGroup(Array.from({ length: 9 }, (_, j) => [i, j]));
      checkGroup(Array.from({ length: 9 }, (_, j) => [j, i]));
    }
    for (let br = 0; br < 9; br += 3) {
      for (let bc = 0; bc < 9; bc += 3) {
        const cells = [];
        for (let r = br; r < br + 3; r++) for (let c = bc; c < bc + 3; c++) cells.push([r, c]);
        checkGroup(cells);
      }
    }
    for (let r = 0; r < 9; r++) {
      for (let c = 0; c < 9; c++) {
        cellEls[r][c].classList.toggle(
          "sk-conflict",
          settings.conflict_highlighting !== false && conflicts.has(r + "," + c)
        );
      }
    }
    return conflicts;
  }

  function persist() {
    if (preview) return;
    window.GameStorage.set("sudoku", slug, {
      grid: state.grid, notes: state.notes, elapsed: state.elapsed,
      mistakes: state.mistakes, hintsUsed: state.hintsUsed, completed: state.completed,
    });
  }

  function restore() {
    if (preview) return;
    const saved = window.GameStorage.get("sudoku", slug);
    if (!saved) return;
    state.grid = saved.grid || state.grid;
    state.notes = saved.notes || state.notes;
    state.elapsed = saved.elapsed || 0;
    state.mistakes = saved.mistakes || 0;
    state.hintsUsed = saved.hintsUsed || 0;
    state.completed = !!saved.completed;
  }

  let timerHandle = null;
  function startTimer() {
    if (!settings.timer_enabled || timerHandle || state.completed || state.paused) return;
    timerHandle = setInterval(() => { state.elapsed += 1; renderStats(); persist(); }, 1000);
  }
  function stopTimer() { if (timerHandle) { clearInterval(timerHandle); timerHandle = null; } }

  function renderStats() {
    if (timerEl) {
      const m = Math.floor(state.elapsed / 60), s = state.elapsed % 60;
      timerEl.textContent = m + ":" + String(s).padStart(2, "0");
    }
    if (mistakesEl) mistakesEl.textContent = state.mistakes;
    if (hintsEl && settings.hints_enabled) {
      const remaining = settings.max_hints ? Math.max(0, settings.max_hints - state.hintsUsed) : "∞";
      hintsEl.textContent = remaining;
    }
  }

  let messageTimeout = null;
  function showMessage(text, kind) {
    messageEl.textContent = text;
    messageEl.className = "sk-message sk-message-" + (kind || "info");
    messageEl.hidden = false;
    if (messageTimeout) clearTimeout(messageTimeout);
    if (kind !== "success-final") messageTimeout = setTimeout(() => { messageEl.hidden = true; }, 3500);
  }

  async function serverCheck() {
    const res = await fetch(checkUrl, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ grid: state.grid }),
    });
    return res.json();
  }

  function paintResults(results) {
    for (let r = 0; r < 9; r++) {
      for (let c = 0; c < 9; c++) {
        const el = cellEls[r][c];
        el.classList.remove("sk-wrong");
        if (isGiven(r, c)) continue;
        if (results[r][c] === false) el.classList.add("sk-wrong");
      }
    }
  }

  async function maybeCheckAfterEntry(r, c) {
    const mode = settings.error_checking || "on_check";
    const full = state.grid.every((row) => row.every((v) => v !== 0));

    if (mode === "immediate") {
      const result = await serverCheck();
      if (result.results[r][c] === false) {
        state.mistakes += 1;
        renderStats();
        cellEls[r][c].classList.add("sk-wrong");
        showMessage("Yanlış rakam.", "error");
      } else {
        cellEls[r][c].classList.remove("sk-wrong");
      }
      if (result.completed) finishPuzzle();
      return;
    }

    if (full) {
      const result = await serverCheck();
      if (mode !== "disabled") paintResults(result.results);
      if (result.completed) finishPuzzle();
      else if (mode !== "disabled") showMessage("Izgara dolu ama henüz hatalı kareler var.", "error");
    }
  }

  function finishPuzzle() {
    state.completed = true;
    stopTimer();
    persist();
    showMessage("Sudoku tamamlandı! Tebrikler.", "success-final");
    const banner = document.getElementById("sk-complete-banner");
    if (banner) {
      banner.hidden = false;
      const summary = banner.querySelector(".sk-complete-summary");
      if (summary) {
        const m = Math.floor(state.elapsed / 60), s = state.elapsed % 60;
        summary.textContent = `Süre: ${m}:${String(s).padStart(2, "0")} · Hata: ${state.mistakes} · İpucu: ${state.hintsUsed}`;
      }
    }
  }

  function setValue(r, c, val) {
    if (isGiven(r, c) || state.paused) return;
    pushUndo();
    if (state.notesMode) {
      if (val === 0) { state.notes[r][c] = []; }
      else {
        const idx = state.notes[r][c].indexOf(val);
        if (idx >= 0) state.notes[r][c].splice(idx, 1); else state.notes[r][c].push(val);
      }
    } else {
      state.grid[r][c] = val;
      state.notes[r][c] = [];
      renderConflicts();
    }
    renderCell(r, c);
    persist();
    if (!state.notesMode && val !== 0) {
      maybeCheckAfterEntry(r, c);
      startTimer();
    }
  }

  gridEl.addEventListener("keydown", (e) => {
    if (!state.selected || state.paused) return;
    const [r, c] = state.selected;
    if (/^[1-9]$/.test(e.key)) { e.preventDefault(); setValue(r, c, parseInt(e.key, 10)); return; }
    if (e.key === "Backspace" || e.key === "Delete" || e.key === "0") {
      e.preventDefault();
      if (!isGiven(r, c)) { pushUndo(); state.grid[r][c] = 0; state.notes[r][c] = []; renderCell(r, c); renderConflicts(); persist(); }
      return;
    }
    const moves = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    if (moves[e.key]) {
      e.preventDefault();
      const [dr, dc] = moves[e.key];
      const nr = Math.min(8, Math.max(0, r + dr));
      const nc = Math.min(8, Math.max(0, c + dc));
      selectCell(nr, nc);
    }
  });
  gridEl.setAttribute("tabindex", "0");

  // ---- number pad (mouse/touch, incl. mobile) ----
  document.querySelectorAll(".sk-numpad-btn[data-n]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!state.selected) return;
      const [r, c] = state.selected;
      setValue(r, c, parseInt(btn.dataset.n, 10));
    });
  });
  document.getElementById("sk-numpad-erase")?.addEventListener("click", () => {
    if (!state.selected) return;
    const [r, c] = state.selected;
    if (isGiven(r, c)) return;
    pushUndo();
    state.grid[r][c] = 0; state.notes[r][c] = [];
    renderCell(r, c); renderConflicts(); persist();
  });

  notesBtn?.addEventListener("click", () => {
    state.notesMode = !state.notesMode;
    notesBtn.classList.toggle("active", state.notesMode);
    notesBtn.setAttribute("aria-pressed", state.notesMode ? "true" : "false");
  });

  document.getElementById("sk-undo")?.addEventListener("click", () => {
    if (!undoStack.length) return;
    redoStack.push(snapshot());
    applySnapshot(undoStack.pop());
  });
  document.getElementById("sk-redo")?.addEventListener("click", () => {
    if (!redoStack.length) return;
    undoStack.push(snapshot());
    applySnapshot(redoStack.pop());
  });

  document.getElementById("sk-check")?.addEventListener("click", async () => {
    const result = await serverCheck();
    paintResults(result.results);
    if (result.completed) finishPuzzle();
    else showMessage("Kontrol edildi — kırmızı işaretli kareler hatalı.", "error");
  });

  document.getElementById("sk-hint")?.addEventListener("click", async () => {
    if (!settings.hints_enabled) return;
    if (settings.max_hints && state.hintsUsed >= settings.max_hints) {
      showMessage("İpucu hakkınız kalmadı.", "info");
      return;
    }
    const res = await fetch(hintUrl, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ grid: state.grid }),
    });
    const data = await res.json();
    if (data.row == null) { showMessage("Verilecek ipucu kalmadı.", "info"); return; }
    pushUndo();
    state.grid[data.row][data.col] = data.value;
    state.notes[data.row][data.col] = [];
    state.hintsUsed += 1;
    renderCell(data.row, data.col);
    renderConflicts();
    renderStats();
    persist();
    showMessage("İpucu eklendi.", "info");
    const full = state.grid.every((row) => row.every((v) => v !== 0));
    if (full) {
      const result = await serverCheck();
      if (result.completed) finishPuzzle();
    }
  });

  document.getElementById("sk-restart")?.addEventListener("click", () => {
    if (!confirm("Tüm ilerlemeniz silinecek. Emin misiniz?")) return;
    state.grid = givens.map((row) => row.slice());
    state.notes = Array.from({ length: 9 }, () => Array.from({ length: 9 }, () => []));
    state.mistakes = 0; state.hintsUsed = 0; state.elapsed = 0; state.completed = false;
    undoStack.length = 0; redoStack.length = 0;
    const banner = document.getElementById("sk-complete-banner");
    if (banner) banner.hidden = true;
    window.GameStorage.clear("sudoku", slug);
    renderAll();
    renderStats();
    showMessage("Sudoku sıfırlandı.", "info");
  });

  const pauseBtn = document.getElementById("sk-pause");
  pauseBtn?.addEventListener("click", () => {
    state.paused = !state.paused;
    pauseBtn.textContent = state.paused ? "Devam Et" : "Duraklat";
    root.classList.toggle("sk-paused", state.paused);
    if (state.paused) stopTimer(); else startTimer();
  });

  // ---- init ----
  restore();
  renderAll();
  renderStats();
  if (state.completed) { const b = document.getElementById("sk-complete-banner"); if (b) b.hidden = false; }
  if (settings.timer_enabled && !state.completed) startTimer();
  selectCell(0, givens[0].every((v) => v) ? 0 : 0);
  // land on first empty cell if possible
  outer:
  for (let r = 0; r < 9; r++) {
    for (let c = 0; c < 9; c++) {
      if (!givens[r][c]) { selectCell(r, c); break outer; }
    }
  }
})();
