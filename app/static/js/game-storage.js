/*
 * Shared localStorage-backed progress storage for crossword/sudoku players.
 * Deliberately a thin key/value wrapper so a future account-based cloud sync
 * layer could implement the same get/set/clear interface as a drop-in swap.
 */
(function (global) {
  "use strict";

  function key(kind, slug) {
    return "eh-game-" + kind + "-" + slug;
  }

  function get(kind, slug) {
    try {
      const raw = window.localStorage.getItem(key(kind, slug));
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function set(kind, slug, data) {
    try {
      window.localStorage.setItem(key(kind, slug), JSON.stringify(data));
    } catch (e) {
      /* storage unavailable (private mode, quota) -- fail silently */
    }
  }

  function clear(kind, slug) {
    try {
      window.localStorage.removeItem(key(kind, slug));
    } catch (e) {
      /* ignore */
    }
  }

  global.GameStorage = { get, set, clear };
})(window);
