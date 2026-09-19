"""Render crossword/sudoku puzzles to print-ready PNGs for the admin panel,
so a finished puzzle can be dropped straight into the next paper issue.

Both games share ONE export pipeline (see _Canvas / _font below):

  - Rendered at SCALE (4x) the on-screen design size from the start --
    every coordinate, line width and font size is `design px * SCALE` --
    never a small image enlarged afterwards.
  - The DEFAULT export is an opaque white RGB image: many phone/desktop
    viewers paint transparent pixels black, so transparency must never be
    the default. Ink is antialiased directly against that white. A separate,
    explicitly labelled transparent RGBA variant ("Şeffaf PNG",
    transparent=True) exists for layout use; there ink is drawn onto alpha 0
    so edge pixels carry ink colour at partial alpha (no background hue to
    fringe against).
  - The typeface is the site's own web font, Libre Franklin (variable
    weight, SIL OFL, vendored in static/fonts/). If it cannot be loaded the
    export FAILS LOUDLY instead of silently falling back to another font.
  - The design constants below mirror style.css's .xw-* / .sk-* rules
    (cell size, 1px cell lines vs 3px 3x3 box lines, 3px/2px frame, font
    weights and colours); tests/test_games_export.py pins them.
"""
import io
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

SCALE = 4

FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts", "LibreFranklin-Variable.ttf")

INK = (26, 26, 26, 255)          # --ink
BLACK = (19, 19, 19, 255)        # --black
USER_BLUE = (43, 95, 176, 255)   # .sk-value (player-entered digits)


class FontUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=64)
def _font(size_px, weight):
    """Libre Franklin at an exact pixel size and CSS weight. No fallback."""
    try:
        font = ImageFont.truetype(FONT_PATH, size_px)
        font.set_variation_by_axes([weight])
    except Exception as exc:
        raise FontUnavailable(f"Libre Franklin could not be loaded from {FONT_PATH}: {exc}") from exc
    return font


class _Canvas:
    """Transparent RGBA canvas in DESIGN pixels, drawn at SCALE."""

    def __init__(self, width, height, transparent=False):
        self.width, self.height = width * SCALE, height * SCALE
        if transparent:
            self.img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        else:
            self.img = Image.new("RGB", (self.width, self.height), (255, 255, 255))
        self.draw = ImageDraw.Draw(self.img)

    def rect(self, x0, y0, x1, y1, fill):
        """Design-pixel rectangle, [x0,x1) x [y0,y1)."""
        self.draw.rectangle([x0 * SCALE, y0 * SCALE, x1 * SCALE - 1, y1 * SCALE - 1], fill=fill)

    def text(self, x, y, s, size, weight, fill, anchor="la"):
        self.draw.text((x * SCALE, y * SCALE), s, fill=fill, font=_font(round(size * SCALE), weight), anchor=anchor)

    def png(self):
        out = io.BytesIO()
        self.img.save(out, format="PNG")
        out.seek(0)
        return out


# ---------------------------------------------------------------- crossword --
XW_CELL = 48          # design px (on-screen max is 64; 48 keeps the old export's proportions)
XW_GAP = 1            # .xw-grid gap
XW_FRAME = 2          # .xw-grid border
XW_MARGIN = 24
XW_TITLE_H = 48


def crossword_dimensions(crossword):
    grid = crossword["grid"]
    h, w = len(grid), (len(grid[0]) if grid else 0)
    board_w = w * XW_CELL + (w - 1) * XW_GAP + 2 * XW_FRAME
    board_h = h * XW_CELL + (h - 1) * XW_GAP + 2 * XW_FRAME
    return board_w, board_h


def build_crossword_png(crossword, transparent=False):
    from games_engine import compute_slots
    grid = crossword["grid"]
    board_w, board_h = crossword_dimensions(crossword)
    width = board_w + 2 * XW_MARGIN
    height = board_h + 2 * XW_MARGIN + XW_TITLE_H
    cv = _Canvas(width, height, transparent)
    numbers, _slots = compute_slots(grid)

    cv.text(XW_MARGIN, XW_MARGIN + 6, crossword.get("title", ""), 20, 700, INK)

    left, top = XW_MARGIN, XW_MARGIN + XW_TITLE_H

    def open_cell(r, c):
        return 0 <= r < len(grid) and 0 <= c < len(grid[0]) and not grid[r][c]["block"]

    # Block cells are simply left as background (white): there is NO dark
    # panel. Every playable cell gets its own ink outline -- 1px lines where
    # it touches another playable cell (shared, as in the on-screen grid gap)
    # and the 2px frame weight on its free sides.
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if cell["block"]:
                continue
            x0 = left + XW_FRAME + c * (XW_CELL + XW_GAP)
            y0 = top + XW_FRAME + r * (XW_CELL + XW_GAP)
            el = XW_GAP if open_cell(r, c - 1) else XW_FRAME
            er = XW_GAP if open_cell(r, c + 1) else XW_FRAME
            et = XW_GAP if open_cell(r - 1, c) else XW_FRAME
            eb = XW_GAP if open_cell(r + 1, c) else XW_FRAME
            cv.rect(x0 - el, y0 - et, x0 + XW_CELL + er, y0 + XW_CELL + eb, BLACK)
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if cell["block"]:
                continue
            x0 = left + XW_FRAME + c * (XW_CELL + XW_GAP)
            y0 = top + XW_FRAME + r * (XW_CELL + XW_GAP)
            cv.rect(x0, y0, x0 + XW_CELL, y0 + XW_CELL, (255, 255, 255, 255))
            num = numbers.get((r, c))
            if num:
                cv.text(x0 + 2, y0 + 1, str(num), 11, 700, INK)   # on screen: --gray-600 is undefined, so it inherits --ink
    return cv.png()


# ------------------------------------------------------------------- sudoku --
SK_CELL = 52          # 480px board / 9, as on screen
SK_THIN = 1           # gap between cells
SK_THICK = 3          # 3x3 box lines and outer frame
SK_DIGIT = 26         # .sk-value max font-size
SK_MARGIN = 24
SK_TITLE_H = 44


def _sk_offsets():
    """Left/top offset of every cell (and the far edge) inside the board."""
    offs, pos = [], SK_THICK
    for i in range(9):
        offs.append(pos)
        pos += SK_CELL + (SK_THICK if i in (2, 5) else SK_THIN)
    return offs, offs[-1] + SK_CELL + SK_THICK


def sudoku_dimensions():
    _offs, board = _sk_offsets()
    return board, board


def build_sudoku_png(sudoku, solved=False, transparent=False):
    grid = sudoku["solution_grid"] if solved else sudoku["starting_grid"]
    given = sudoku["starting_grid"]
    offs, board = _sk_offsets()
    cv = _Canvas(board + 2 * SK_MARGIN, board + 2 * SK_MARGIN + SK_TITLE_H, transparent)
    cv.text(SK_MARGIN, SK_MARGIN + 4, sudoku.get("title", ""), 20, 700, INK)

    left, top = SK_MARGIN, SK_MARGIN + SK_TITLE_H
    cv.rect(left, top, left + board, top + board, BLACK)          # frame + all lines
    for r in range(9):
        for c in range(9):
            x0, y0 = left + offs[c], top + offs[r]
            cv.rect(x0, y0, x0 + SK_CELL, y0 + SK_CELL, (255, 255, 255, 255))
            val = grid[r][c]
            if not val:
                continue
            is_given = given[r][c] != 0
            cv.text(x0 + SK_CELL / 2, y0 + SK_CELL / 2, str(val), SK_DIGIT, 800 if is_given else 400,
                    INK if is_given else USER_BLUE, anchor="mm")
    return cv.png()
