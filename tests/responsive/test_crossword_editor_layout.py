"""Crossword EDITOR layout regression: compares real bounding rectangles.

Root cause being guarded (see style.css .xw-build-layout): the board column had
no size container, so the shared grid's `cqw`-based cell size fell back to the
viewport (64px cells) and an inline max-width:620px let the fixed-width grid run
out of its 560px column, underneath the clue panel."""
import json
import os

import pytest

DATA = os.path.join(os.path.dirname(__file__), "..", "..", "app", "data")
SHOTS = os.path.join(os.path.dirname(__file__), "screenshots")
BREAKPOINT = 900          # <= 900px: fully stacked; >= 901px: two columns
WIDTHS = [768, 900, 901, 1024, 1280, 1440, 1920]
LONG = "Çok uzun bir ipucu metni " + "kesintisizuzunbirsözcük" * 6 + " ve devamı bir cümle daha " * 3


@pytest.fixture(scope="module")
def cw(live_server):
    import store
    c = json.load(open(os.path.join(DATA, "crosswords.json")))[0]
    for cl in c["clues"]:
        cl["clue"] = LONG
    store.save_crosswords([c])
    return c


def rects(page):
    return page.evaluate("""() => {
      const R = e => { const b = e.getBoundingClientRect(); return {l: b.left, r: b.right, t: b.top, b: b.bottom, w: b.width, h: b.height}; };
      const all = (s, root=document) => [...root.querySelectorAll(s)].map(R);
      const q = s => { const e = document.querySelector(s); return e ? R(e) : null; };
      const side = document.querySelector('.xw-build-side');
      const main = document.querySelector('.xw-build-main');
      const editor = document.querySelector('.xw-clue-editor');
      return {
        vw: innerWidth, docOverflow: document.documentElement.scrollWidth - innerWidth,
        panel: q('.admin-panel:has(.xw-build-layout)') || q('.xw-build-layout').parent,
        layout: q('.xw-build-layout'), main: R(main), side: R(side), editor: R(editor), grid: q('#xwb-grid'),
        editorScrolls: editor.scrollHeight > editor.clientHeight + 1,
        sideKids: {
          headings: all('h3', side), rows: all('.clue-row', side), nums: all('.clue-row .num', side),
          inputs: all('.clue-row input', side), answers: all('.clue-row .ans', side),
        },
        mainKids: {
          buttons: all('.xw-build-toolbar .btn', main), toolbar: all('.xw-build-toolbar', main), hint: all('.xw-build-hint', main),
          savebar: all('.xw-build-save-bar', main), status: all('.xw-build-main form', main),
          hints: all('.xw-build-main > .hint', main),
        },
        cells: all('#xwb-grid .xwb-cell'),
        acrossRows: all('#xwb-across-editor .clue-row'), downRows: all('#xwb-down-editor .clue-row'),
      };
    }""")


def hit(a, b, tol=0.5):
    return a["l"] < b["r"] - tol and b["l"] < a["r"] - tol and a["t"] < b["b"] - tol and b["t"] < a["b"] - tol


def inside(child, parent, tol=1.0):
    return child["l"] >= parent["l"] - tol and child["r"] <= parent["r"] + tol and \
        child["t"] >= parent["t"] - tol and child["b"] <= parent["b"] + tol


@pytest.mark.parametrize("height", [700, 1000])
@pytest.mark.parametrize("width", WIDTHS)
def test_editor_layout_never_overlaps(admin_page, live_server, cw, width, height):
    page = admin_page
    page.set_viewport_size({"width": width, "height": height})
    page.goto(live_server["base_url"] + f"/admin/oyunlar/bulmaca/{cw['id']}/duzenle")
    page.wait_for_load_state("networkidle")
    d = rects(page)
    two_col = width > BREAKPOINT

    # 7. no document-level horizontal overflow
    assert d["docOverflow"] <= 1, f"{width}: page overflows by {d['docOverflow']}px"
    # 6. neither primary panel exceeds its parent
    assert inside(d["main"], d["layout"]) and inside(d["side"], d["layout"]), f"{width}: a column exceeds the layout"
    assert d["layout"]["r"] <= d["vw"] + 0.5 and d["layout"]["l"] >= -0.5
    # 1 + 8. columns never intersect; two-column above the breakpoint, fully stacked at/below it
    assert not hit(d["main"], d["side"]), f"{width}: columns intersect"
    if two_col:
        assert d["side"]["l"] >= d["main"]["r"] + 29, f"{width}: gap between columns is only {d['side']['l'] - d['main']['r']}px"
        assert abs(d["side"]["t"] - d["main"]["t"]) < 2, "columns are not side by side"
    else:
        assert abs(d["side"]["l"] - d["main"]["l"]) < 2 and d["side"]["t"] >= d["main"]["b"] - 0.5, \
            f"{width}: clue panel is not fully stacked below the editor column"
        assert d["side"]["w"] >= d["main"]["w"] - 2
    # 2. grid vs every visible clue-panel child
    grid = d["grid"]
    for group, items in d["sideKids"].items():
        for it in items:
            assert not hit(grid, it), f"{width}: grid intersects clue {group}"
    assert not hit(grid, d["editor"]) and not hit(grid, d["side"])
    # the grid stays in its own column, square cells preserved
    assert inside(grid, d["main"]), f"{width}: grid leaves its column ({grid['l']}..{grid['r']} vs {d['main']['l']}..{d['main']['r']})"
    assert d["cells"] and all(abs(c["w"] - c["h"]) < 0.6 for c in d["cells"]), "cells are not square"
    # 3. rows don't intersect each other or the headings
    for rows in (d["acrossRows"], d["downRows"]):
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                assert not hit(a, b), f"{width}: clue rows overlap"
    everything = d["sideKids"]["rows"] + d["sideKids"]["headings"]
    for i, a in enumerate(d["sideKids"]["headings"]):
        for r in d["sideKids"]["rows"]:
            assert not hit(a, r), f"{width}: heading overlaps a row"
    # 4. every clue child stays inside the clue panel; answers never cover inputs
    assert not d["editorScrolls"], "fixture should not need the editor's inner scroll"
    for group, items in d["sideKids"].items():
        for it in items:
            assert inside(it, d["editor"]), f"{width}: {group} leaves the clue panel"
            assert inside(it, d["side"]), f"{width}: {group} leaves the side column"
    for a, i, n in zip(d["sideKids"]["answers"], d["sideKids"]["inputs"], d["sideKids"]["nums"]):
        assert not hit(a, i) and not hit(n, i) and not hit(a, n), f"{width}: answer/number collides with input"
    for row, inp, ans in zip(d["sideKids"]["rows"], d["sideKids"]["inputs"], d["sideKids"]["answers"]):
        assert inside(inp, row) and inside(ans, row, tol=1.0), f"{width}: input/answer escapes its row"
    # 5. buttons, hint text, save bar stay in the crossword column
    for group, items in d["mainKids"].items():
        for it in items:
            assert inside(it, d["main"]), f"{width}: {group} leaves the crossword column"
            assert not hit(it, d["side"]), f"{width}: {group} runs under the clue panel"
            if group in ("buttons", "toolbar", "hint", "savebar", "status", "hints"):
                assert not hit(it, grid), f"{width}x{height}: {group} overlaps the crossword grid"


@pytest.mark.parametrize("name,width", [("wide_two_column", 1440), ("narrow_two_column_before_breakpoint", 901),
                                        ("stacked_after_breakpoint", 900)])
def test_capture_editor_layouts(admin_page, live_server, cw, name, width):
    admin_page.set_viewport_size({"width": width, "height": 1000})
    admin_page.goto(live_server["base_url"] + f"/admin/oyunlar/bulmaca/{cw['id']}/duzenle")
    admin_page.wait_for_load_state("networkidle")
    os.makedirs(SHOTS, exist_ok=True)
    admin_page.screenshot(path=os.path.join(SHOTS, f"crossword_editor_{name}.png"), full_page=True)
