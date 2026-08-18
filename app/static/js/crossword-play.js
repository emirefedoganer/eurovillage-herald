/*
 * Interactive crossword player.
 * Keyboard handling is scoped entirely to the crossword grid element (each
 * cell is a real focusable DOM node) so it never interferes with normal page
 * or form-field typing elsewhere.
 */
(function () {
  "use strict";

  const root = document.getElementById("crossword-root");
  if (!root) return;

  const payload = JSON.parse(document.getElementById("crossword-data").textContent);
  const { slug, width, height, grid, clues, settings } = payload;
  const preview = root.dataset.preview === "1";
  const checkUrl = root.dataset.checkUrl;

  const gridEl = document.getElementById("xw-grid");
  const messageEl = document.getElementById("xw-message");
  const timerEl = document.getElementById("xw-timer");
  const acrossListEl = document.getElementById("xw-across-list");
  const downListEl = document.getElementById("xw-down-list");

  // ---- derive per-cell slot membership + per-slot cell lists ----
  const cellSlot = {}; // "r,c" -> {across: num|undefined, down: num|undefined}
  const slotCells = {}; // "num_dir" -> [[r,c], ...]
  const slotByNumDir = {};
  clues.forEach((cl) => {
    const cells = [];
    for (let i = 0; i < cl.length; i++) {
      const r = cl.direction === "down" ? cl.row + i : cl.row;
      const c = cl.direction === "across" ? cl.col + i : cl.col;
      const k = r + "," + c;
      if (!cellSlot[k]) cellSlot[k] = {};
      cellSlot[k][cl.direction] = cl.number;
      cells.push([r, c]);
    }
    slotCells[cl.number + "_" + cl.direction] = cells;
    slotByNumDir[cl.number + "_" + cl.direction] = cl;
  });

  // ---- build grid DOM ----
  const cellEls = {}; // "r,c" -> element
  gridEl.style.setProperty("--xw-cols", width);
  gridEl.style.setProperty("--xw-rows", height);

  for (let r = 0; r < height; r++) {
    for (let c = 0; c < width; c++) {
      const cellData = grid[r][c];
      const cell = document.createElement(cellData.block ? "div" : "button");
      cell.className = "xw-cell" + (cellData.block ? " xw-block" : "");
      if (!cellData.block) {
        cell.type = "button";
        cell.setAttribute("role", "gridcell");
        cell.tabIndex = -1;
        cell.dataset.r = r;
        cell.dataset.c = c;
        if (cellData.number) {
          const num = document.createElement("span");
          num.className = "xw-number";
          num.textContent = cellData.number;
          cell.appendChild(num);
        }
        const letter = document.createElement("span");
        letter.className = "xw-letter";
        cell.appendChild(letter);
        cell.setAttribute("aria-label", "Satır " + (r + 1) + ", sütun " + (c + 1));
        cellEls[r + "," + c] = cell;
      } else {
        cell.setAttribute("aria-hidden", "true");
      }
      gridEl.appendChild(cell);
    }
  }

  // A single hidden text input, focused instead of the cell buttons.
  // Buttons can't summon a mobile on-screen keyboard; a real text input
  // can. Its "input" event (not keydown) is the letter-entry path so it
  // works identically for a virtual keyboard's tap-to-type and a physical
  // keyboard's keypress. It stays nested inside gridEl so Backspace/
  // Delete/Arrow keys still bubble to the existing delegated listener.
  const mobileInput = document.createElement("input");
  mobileInput.type = "text";
  mobileInput.className = "xw-mobile-input";
  mobileInput.setAttribute("autocomplete", "off");
  mobileInput.setAttribute("autocorrect", "off");
  mobileInput.setAttribute("autocapitalize", "off");
  mobileInput.setAttribute("spellcheck", "false");
  mobileInput.setAttribute("aria-hidden", "true");
  mobileInput.tabIndex = -1;
  gridEl.appendChild(mobileInput);

  function focusActiveCell() {
    const cell = cellEls[state.row + "," + state.col];
    if (cell) mobileInput.setAttribute("aria-label", cell.getAttribute("aria-label") || "");
    mobileInput.focus({ preventScroll: true });
  }

  // ---- clue lists ----
  function renderClueList(container, direction) {
    container.innerHTML = "";
    clues
      .filter((cl) => cl.direction === direction)
      .sort((a, b) => a.number - b.number)
      .forEach((cl) => {
        const li = document.createElement("li");
        li.className = "xw-clue-item";
        li.dataset.number = cl.number;
        li.dataset.direction = cl.direction;
        li.tabIndex = 0;
        li.innerHTML = '<span class="xw-clue-num">' + cl.number + "</span> " +
          '<span class="xw-clue-text">' + (cl.clue || "(ipucu girilmemiş)") + "</span>";
        li.addEventListener("click", () => selectSlot(cl.number, cl.direction, true));
        li.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            selectSlot(cl.number, cl.direction, true);
          }
        });
        container.appendChild(li);
      });
  }
  renderClueList(acrossListEl, "across");
  renderClueList(downListEl, "down");

  // ---- state ----
  const state = {
    row: null,
    col: null,
    direction: "across",
    entries: {}, // "r,c" -> letter
    elapsed: 0,
    completed: false,
    hintsUsed: 0,
  };

  let timerHandle = null;
  function startTimer() {
    if (!settings.timer_enabled || timerHandle || state.completed) return;
    timerHandle = setInterval(() => {
      state.elapsed += 1;
      renderTimer();
      persist();
    }, 1000);
  }
  function stopTimer() {
    if (timerHandle) { clearInterval(timerHandle); timerHandle = null; }
  }
  function renderTimer() {
    if (!timerEl) return;
    const m = Math.floor(state.elapsed / 60);
    const s = state.elapsed % 60;
    timerEl.textContent = m + ":" + String(s).padStart(2, "0");
  }

  function persist() {
    if (preview) return;
    window.GameStorage.set("crossword", slug, {
      entries: state.entries,
      elapsed: state.elapsed,
      completed: state.completed,
      hintsUsed: state.hintsUsed,
    });
  }

  function restore() {
    if (preview) return;
    const saved = window.GameStorage.get("crossword", slug);
    if (!saved) return;
    state.entries = saved.entries || {};
    state.elapsed = saved.elapsed || 0;
    state.completed = !!saved.completed;
    state.hintsUsed = saved.hintsUsed || 0;
    Object.keys(state.entries).forEach((k) => {
      const el = cellEls[k];
      if (el) el.querySelector(".xw-letter").textContent = state.entries[k];
    });
    renderTimer();
    if (state.completed) showCompletion(false);
  }

  function findSlotAt(r, c, direction) {
    const info = cellSlot[r + "," + c];
    if (!info) return null;
    let dir = direction;
    if (!(dir in info)) dir = Object.keys(info)[0];
    if (!dir) return null;
    return { number: info[dir], direction: dir };
  }

  function clearHighlights() {
    Object.values(cellEls).forEach((el) => el.classList.remove("xw-selected", "xw-in-word"));
    document.querySelectorAll(".xw-clue-item.active").forEach((el) => el.classList.remove("active"));
  }

  function selectCell(r, c, direction) {
    if (r == null || c == null) return;
    const key = r + "," + c;
    if (!cellEls[key]) return;
    const slot = findSlotAt(r, c, direction || state.direction);
    if (!slot) return;
    state.row = r;
    state.col = c;
    state.direction = slot.direction;
    highlight();
    focusActiveCell();
  }

  function selectSlot(number, direction, focusFirst) {
    const cells = slotCells[number + "_" + direction];
    if (!cells) return;
    state.direction = direction;
    state.row = cells[0][0];
    state.col = cells[0][1];
    highlight();
    if (focusFirst) focusActiveCell();
  }

  function highlight() {
    clearHighlights();
    if (state.row == null) return;
    const slot = findSlotAt(state.row, state.col, state.direction);
    if (!slot) return;
    const cells = slotCells[slot.number + "_" + slot.direction] || [];
    cells.forEach(([r, c]) => {
      const el = cellEls[r + "," + c];
      if (el) el.classList.add("xw-in-word");
    });
    const selEl = cellEls[state.row + "," + state.col];
    if (selEl) selEl.classList.add("xw-selected");
    const activeClue = document.querySelector(
      '.xw-clue-item[data-number="' + slot.number + '"][data-direction="' + slot.direction + '"]'
    );
    if (activeClue) {
      activeClue.classList.add("active");
      activeClue.scrollIntoView({ block: "nearest" });
    }
  }

  function setLetter(r, c, ch) {
    const key = r + "," + c;
    const el = cellEls[key];
    if (!el) return;
    el.classList.remove("xw-incorrect", "xw-correct");
    if (ch) {
      state.entries[key] = ch;
    } else {
      delete state.entries[key];
    }
    el.querySelector(".xw-letter").textContent = ch || "";
    persist();
  }

  function nextCellInWord(step) {
    const slot = findSlotAt(state.row, state.col, state.direction);
    if (!slot) return null;
    const cells = slotCells[slot.number + "_" + slot.direction];
    const idx = cells.findIndex(([r, c]) => r === state.row && c === state.col);
    const next = cells[idx + step];
    return next || null;
  }

  function moveArrow(dr, dc) {
    let r = state.row, c = state.col;
    for (let step = 0; step < Math.max(width, height); step++) {
      r += dr; c += dc;
      if (r < 0 || r >= height || c < 0 || c >= width) return;
      if (cellEls[r + "," + c]) {
        const dir = dr !== 0 ? "down" : "across";
        selectCell(r, c, cellSlot[r + "," + c] && dir in cellSlot[r + "," + c] ? dir : state.direction);
        return;
      }
    }
  }

  gridEl.addEventListener("click", (e) => {
    const cell = e.target.closest(".xw-cell:not(.xw-block)");
    if (!cell) return;
    const r = parseInt(cell.dataset.r, 10), c = parseInt(cell.dataset.c, 10);
    if (state.row === r && state.col === c) {
      const info = cellSlot[r + "," + c] || {};
      const other = state.direction === "across" ? "down" : "across";
      if (other in info) {
        state.direction = other;
        highlight();
        return;
      }
    }
    selectCell(r, c, state.direction);
  });

  const LETTER_RE = /^[a-zA-ZçğıöşüÇĞİÖŞÜ]$/;

  // Letter entry (both physical and virtual/mobile keyboards) goes through
  // the hidden input's "input" event below, not keydown -- keydown here
  // only handles the non-character keys, which don't produce input events.
  mobileInput.addEventListener("input", () => {
    const typed = mobileInput.value;
    mobileInput.value = "";
    if (state.row == null || !typed) return;
    const ch = typed.slice(-1);
    if (!LETTER_RE.test(ch)) return;
    setLetter(state.row, state.col, ch.toLocaleUpperCase("tr-TR"));
    const next = nextCellInWord(1);
    if (next) selectCell(next[0], next[1], state.direction); else highlight();
    startTimer();
  });

  gridEl.addEventListener("keydown", (e) => {
    if (state.row == null) return;
    if (e.key === "Backspace") {
      e.preventDefault();
      const key = state.row + "," + state.col;
      if (state.entries[key]) {
        setLetter(state.row, state.col, "");
      } else {
        const prev = nextCellInWord(-1);
        if (prev) {
          selectCell(prev[0], prev[1], state.direction);
          setLetter(prev[0], prev[1], "");
        }
      }
      return;
    }
    if (e.key === "Delete") {
      e.preventDefault();
      setLetter(state.row, state.col, "");
      return;
    }
    if (e.key === "ArrowRight") { e.preventDefault(); moveArrow(0, 1); return; }
    if (e.key === "ArrowLeft") { e.preventDefault(); moveArrow(0, -1); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); moveArrow(1, 0); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); moveArrow(-1, 0); return; }
    if (e.key === "Tab") {
      // let Tab move focus normally between cells/controls; direction stays.
    }
  });

  // ---- message helper ----
  let messageTimeout = null;
  function showMessage(text, kind) {
    messageEl.textContent = text;
    messageEl.className = "xw-message xw-message-" + (kind || "info");
    messageEl.hidden = false;
    if (messageTimeout) clearTimeout(messageTimeout);
    if (kind !== "success-final") {
      messageTimeout = setTimeout(() => { messageEl.hidden = true; }, 4000);
    }
  }

  function showCompletion(announce) {
    state.completed = true;
    stopTimer();
    persist();
    if (announce) showMessage("Tebrikler! Bulmacayı tamamladınız.", "success-final");
    const banner = document.getElementById("xw-complete-banner");
    if (banner) banner.hidden = false;
  }

  async function postCheck(body) {
    const res = await fetch(checkUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.json();
  }

  // ---- controls ----
  document.getElementById("xw-check-letter")?.addEventListener("click", async () => {
    if (state.row == null) { showMessage("Önce bir kare seçin.", "info"); return; }
    const key = state.row + "," + state.col;
    const value = state.entries[key];
    if (!value) { showMessage("Önce bu kareye bir harf girin.", "info"); return; }
    const result = await postCheck({ mode: "letter", row: state.row, col: state.col, value });
    const el = cellEls[key];
    if (result.correct) {
      el.classList.add("xw-correct");
      showMessage("Doğru harf!", "success");
    } else {
      el.classList.add("xw-incorrect");
      showMessage("Yanlış harf.", "error");
    }
  });

  document.getElementById("xw-check-word")?.addEventListener("click", async () => {
    if (state.row == null) return;
    const slot = findSlotAt(state.row, state.col, state.direction);
    if (!slot) return;
    const cells = slotCells[slot.number + "_" + slot.direction];
    const values = cells.map(([r, c]) => state.entries[r + "," + c] || "");
    if (values.some((v) => !v)) { showMessage("Önce kelimenin tamamını doldurun.", "info"); return; }
    const result = await postCheck({ mode: "word", cells, values });
    cells.forEach(([r, c], i) => {
      const el = cellEls[r + "," + c];
      el.classList.remove("xw-correct", "xw-incorrect");
      el.classList.add(result.results[i] ? "xw-correct" : "xw-incorrect");
    });
    showMessage(result.correct ? "Bu kelime doğru!" : "Bu kelime yanlış.", result.correct ? "success" : "error");
  });

  document.getElementById("xw-check-puzzle")?.addEventListener("click", async () => {
    const result = await postCheck({ mode: "puzzle", cells: state.entries });
    let wrong = 0, empty = 0;
    Object.keys(result.results).forEach((key) => {
      const el = cellEls[key];
      if (!el) return;
      el.classList.remove("xw-correct", "xw-incorrect");
      const v = result.results[key];
      if (v === true) el.classList.add("xw-correct");
      else if (v === false) { el.classList.add("xw-incorrect"); wrong++; }
      else empty++;
    });
    if (result.completed) {
      showCompletion(true);
    } else {
      showMessage(`Henüz tamamlanmadı: ${wrong} yanlış, ${empty} boş kare.`, "error");
    }
  });

  document.getElementById("xw-reveal")?.addEventListener("click", () => {
    if (!settings.reveal_answer) return;
    if (!confirm("Bu kelimenin cevabını görmek istediğinize emin misiniz?")) return;
    // Reveal works via the check-word answer channel would leak the key, so
    // instead we ask the server directly for this one slot's answer.
    if (state.row == null) return;
    const slot = findSlotAt(state.row, state.col, state.direction);
    if (!slot) return;
    postCheck({ mode: "reveal", number: slot.number, direction: slot.direction }).then((result) => {
      if (!result.answer) return;
      const cells = slotCells[slot.number + "_" + slot.direction];
      cells.forEach(([r, c], i) => {
        setLetter(r, c, result.answer[i]);
        cellEls[r + "," + c].classList.add("xw-correct");
      });
      showMessage("Cevap gösterildi.", "info");
    });
  });

  document.getElementById("xw-restart")?.addEventListener("click", () => {
    if (!confirm("Tüm ilerlemeniz silinecek. Emin misiniz?")) return;
    state.entries = {};
    state.elapsed = 0;
    state.completed = false;
    state.hintsUsed = 0;
    Object.values(cellEls).forEach((el) => {
      el.querySelector(".xw-letter").textContent = "";
      el.classList.remove("xw-correct", "xw-incorrect");
    });
    renderTimer();
    const banner = document.getElementById("xw-complete-banner");
    if (banner) banner.hidden = true;
    window.GameStorage.clear("crossword", slug);
    showMessage("Bulmaca sıfırlandı.", "info");
  });

  // ---- init ----
  restore();
  renderTimer();
  const firstSlot = clues.slice().sort((a, b) => a.number - b.number)[0];
  if (firstSlot) selectSlot(firstSlot.number, firstSlot.direction, false);
  if (settings.timer_enabled && !state.completed) startTimer();
})();
