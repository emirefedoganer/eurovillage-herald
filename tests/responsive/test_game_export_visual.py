"""Website-vs-export comparison for both games: the on-screen computed
typography/line geometry must equal what the PNG exporter draws, and a
side-by-side pair of images is written to screenshots/ for a human check."""
import io
import json
import os

from PIL import Image

import games_export as g

SHOTS = os.path.join(os.path.dirname(__file__), "screenshots")
DATA = os.path.join(os.path.dirname(__file__), "..", "..", "app", "data")


def _seed_games(live_server):
    import store
    store.save_sudokus([dict(json.load(open(os.path.join(DATA, "sudokus.json")))[0], status="published")])
    store.save_crosswords([dict(json.load(open(os.path.join(DATA, "crosswords.json")))[0], status="published")])
    return store.load_sudokus()[0], store.load_crosswords()[0]


def test_sudoku_export_matches_on_screen_design(live_server, browser):
    sudoku, crossword = _seed_games(live_server)
    context = browser.new_context(viewport={"width": 900, "height": 1000}, device_scale_factor=4)
    page = context.new_page()
    page.goto(live_server["base_url"] + f"/oyun-kosesi/sudoku/{sudoku['slug']}")
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.load('800 26px \"Libre Franklin\"')")
    page.evaluate("document.fonts.load('400 26px \"Libre Franklin\"')")
    assert page.evaluate("document.fonts.check('800 26px \"Libre Franklin\"')"), "web font not loaded"

    s = page.evaluate("""() => {
      const cell = document.querySelector('.sk-cell'); const grid = document.querySelector('.sk-grid');
      const given = document.querySelector('.sk-cell.sk-given .sk-value');
      const gs = getComputedStyle(grid), gv = getComputedStyle(given);
      const third = getComputedStyle(document.querySelectorAll('.sk-cell')[2]);
      return {cellW: cell.getBoundingClientRect().width, gridW: grid.getBoundingClientRect().width,
              fam: gv.fontFamily, weight: gv.fontWeight, size: parseFloat(gv.fontSize), color: gv.color,
              frame: parseFloat(gs.borderTopWidth), gap: parseFloat(gs.columnGap), thick: parseFloat(third.borderRightWidth)};
    }""")
    assert "Libre Franklin" in s["fam"] and s["weight"] == "800"
    assert s["frame"] == g.SK_THICK and s["thick"] == g.SK_THICK and s["gap"] == g.SK_THIN
    # digit-to-cell proportion on screen == in the export
    assert abs(s["size"] / s["cellW"] - g.SK_DIGIT / g.SK_CELL) < 0.03, s

    os.makedirs(SHOTS, exist_ok=True)
    grid_png = page.locator(".sk-grid").screenshot()
    export = Image.open(g.build_sudoku_png(sudoku))
    board = g.sudoku_dimensions()[0]
    export_board = export.crop((g.SK_MARGIN * 4, (g.SK_MARGIN + g.SK_TITLE_H) * 4,
                                (g.SK_MARGIN + board) * 4, (g.SK_MARGIN + g.SK_TITLE_H + board) * 4))
    site = Image.open(io.BytesIO(grid_png)).convert("RGBA")
    backdrop = export_board.convert("RGBA")   # the default export is already opaque white
    site = site.resize(backdrop.size)
    pair = Image.new("RGB", (backdrop.width * 2 + 20, backdrop.height), "white")
    pair.paste(site.convert("RGB"), (0, 0))
    pair.paste(backdrop.convert("RGB"), (backdrop.width + 20, 0))
    pair.save(os.path.join(SHOTS, "sudoku_site_vs_export.png"))
    # coarse structural agreement: dark-pixel coverage of the two renderings
    def coverage(im):
        gray = im.convert("L")
        return sum(1 for v in gray.getdata() if v < 128) / (gray.width * gray.height)
    assert abs(coverage(site) - coverage(backdrop)) < 0.02
    context.close()


def test_crossword_export_matches_on_screen_grid_geometry(live_server, browser):
    sudoku, crossword = _seed_games(live_server)
    context = browser.new_context(viewport={"width": 1200, "height": 1000}, device_scale_factor=2)
    page = context.new_page()
    page.goto(live_server["base_url"] + f"/oyun-kosesi/bulmaca/{crossword['slug']}")
    page.wait_for_load_state("networkidle")
    s = page.evaluate("""() => { const grid = document.querySelector('.xw-grid'); const gs = getComputedStyle(grid);
      return {frame: parseFloat(gs.borderTopWidth), gap: parseFloat(gs.columnGap), bg: gs.backgroundColor,
              cols: document.querySelectorAll('.xw-cell').length}; }""")
    assert s["frame"] == g.XW_FRAME and s["gap"] == g.XW_GAP
    assert s["cols"] == crossword["width"] * crossword["height"]
    os.makedirs(SHOTS, exist_ok=True)
    page.locator(".xw-grid").screenshot(path=os.path.join(SHOTS, "crossword_site.png"))
    Image.open(g.build_crossword_png(crossword)).save(os.path.join(SHOTS, "crossword_export_raw.png"))
    context.close()
