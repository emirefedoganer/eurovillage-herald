#!/usr/bin/env python3
"""Puzzle / Sudoku PNG export: transparency, resolution, typography,
completeness. Pure Pillow -- no browser needed."""
import io
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "app"))

from PIL import Image  # noqa: E402

import games_export as g  # noqa: E402

GRID = [
    [{"block": True, "solution": ""}, {"block": False, "solution": "A"}, {"block": False, "solution": "B"}, {"block": True, "solution": ""}],
    [{"block": False, "solution": "C"}, {"block": False, "solution": "D"}, {"block": False, "solution": "E"}, {"block": False, "solution": "F"}],
    [{"block": True, "solution": ""}, {"block": False, "solution": "G"}, {"block": True, "solution": ""}, {"block": False, "solution": "H"}],
]
CROSSWORD = {"title": "Deneme Bulmaca", "grid": GRID}
START = [[(r * 3 + r // 3 + c) % 9 + 1 if (r + c) % 2 == 0 else 0 for c in range(9)] for r in range(9)]
SOLUTION = [[(r * 3 + r // 3 + c) % 9 + 1 for c in range(9)] for r in range(9)]
SUDOKU = {"title": "Günün Sudokusu", "starting_grid": START, "solution_grid": SOLUTION}
CSS = open(os.path.join(REPO_ROOT, "app", "static", "css", "style.css"), encoding="utf-8").read()


def _open(buf):
    return Image.open(io.BytesIO(buf.getvalue()))


def _is_cyan(px):
    r, gg, b, a = px
    return a > 0 and r < 120 and gg > 190 and b > 190


class TransparencyTests(unittest.TestCase):
    def test_both_exports_are_rgba_png_with_zero_alpha_outside_the_artwork(self):
        for name, buf in (("crossword", g.build_crossword_png(CROSSWORD)), ("sudoku", g.build_sudoku_png(SUDOKU))):
            im = _open(buf)
            self.assertEqual(im.format, "PNG", name)
            self.assertEqual(im.mode, "RGBA", name)
            w, h = im.size
            for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 2)):
                self.assertEqual(im.getpixel(xy)[3], 0, f"{name} background at {xy} is not alpha 0")
            self.assertGreater(im.getchannel("A").getextrema()[1], 0)

    def test_no_cyan_or_stray_hues_anywhere(self):
        for buf in (g.build_crossword_png(CROSSWORD), g.build_sudoku_png(SUDOKU, solved=True)):
            im = _open(buf)
            for px in set(im.getdata()):
                self.assertFalse(_is_cyan(px), px)
            # every partially-transparent (antialiased) pixel carries ink
            # colour -- black/grey/blue text -- never a background hue
            for r, gg, b, a in set(im.getdata()):
                if 0 < a < 255:
                    self.assertLess(max(r, gg, b), 200, (r, gg, b, a))
                    self.assertFalse(gg > r + 80 and b > r + 80 and r < 120 and gg > 150 and abs(gg - b) < 30)

    def test_transparent_pixels_carry_no_colour_information_to_fringe(self):
        im = _open(g.build_crossword_png(CROSSWORD))
        self.assertEqual(im.getpixel((0, 0)), (0, 0, 0, 0))


class ResolutionTests(unittest.TestCase):
    def test_pixel_dimensions_are_scale_times_design_size(self):
        self.assertEqual(g.SCALE, 4)
        bw, bh = g.crossword_dimensions(CROSSWORD)
        im = _open(g.build_crossword_png(CROSSWORD))
        self.assertEqual(im.size, ((bw + 2 * g.XW_MARGIN) * 4, (bh + 2 * g.XW_MARGIN + g.XW_TITLE_H) * 4))
        sw, sh = g.sudoku_dimensions()
        im = _open(g.build_sudoku_png(SUDOKU))
        self.assertEqual(im.size, ((sw + 2 * g.SK_MARGIN) * 4, (sh + 2 * g.SK_MARGIN + g.SK_TITLE_H) * 4))

    def test_old_export_dimensions_for_reference(self):
        # previous Pillow export: 9*56 + 2*30 = 564 wide for sudoku -- the new one is >3x that in cell pixels
        self.assertGreaterEqual(g.SK_CELL * g.SCALE, 3 * 56)


class BoundsTests(unittest.TestCase):
    def test_crossword_board_is_complete_and_uncropped(self):
        bw, bh = g.crossword_dimensions(CROSSWORD)
        im = _open(g.build_crossword_png(CROSSWORD))
        top = (g.XW_MARGIN + g.XW_TITLE_H) * 4
        board = im.crop((0, top, im.width, im.height))
        self.assertEqual(board.getchannel("A").getbbox(), (g.XW_MARGIN * 4, 0, (g.XW_MARGIN + bw) * 4, bh * 4))
        # margin all around is untouched
        self.assertEqual(im.crop((0, 0, g.XW_MARGIN * 4 - 1, im.height)).getchannel("A").getbbox(), None)

    def test_sudoku_board_is_complete_and_uncropped(self):
        bw, _ = g.sudoku_dimensions()
        im = _open(g.build_sudoku_png(SUDOKU))
        top = (g.SK_MARGIN + g.SK_TITLE_H) * 4
        board = im.crop((0, top, im.width, im.height))
        self.assertEqual(board.getchannel("A").getbbox(), (g.SK_MARGIN * 4, 0, (g.SK_MARGIN + bw) * 4, bw * 4))
        title = im.crop((0, 0, im.width, top)).getchannel("A").getbbox()
        self.assertGreater(title[0], 0)
        self.assertLess(title[2], im.width)

    def test_crossword_blocks_numbers_and_cells_are_all_present(self):
        im = _open(g.build_crossword_png(CROSSWORD))
        left, top = g.XW_MARGIN, g.XW_MARGIN + g.XW_TITLE_H

        def centre(r, c):
            x = left + g.XW_FRAME + c * (g.XW_CELL + g.XW_GAP) + g.XW_CELL // 2
            y = top + g.XW_FRAME + r * (g.XW_CELL + g.XW_GAP) + g.XW_CELL // 2
            return im.getpixel((x * 4, y * 4))
        self.assertEqual(centre(0, 0)[:3], (19, 19, 19))       # block
        self.assertEqual(centre(0, 1), (255, 255, 255, 255))   # letter cell
        # number "1" sits in the top-left of cell (0,1)
        x0 = left + g.XW_FRAME + 1 * (g.XW_CELL + g.XW_GAP)
        y0 = top + g.XW_FRAME
        num = im.crop(((x0 + 1) * 4, (y0) * 4, (x0 + 14) * 4, (y0 + 14) * 4))
        self.assertTrue(any(p[0] < 140 for p in num.getdata()))


class SudokuTypographyAndGridTests(unittest.TestCase):
    def test_uses_the_real_web_font_and_never_falls_back(self):
        f = g._font(104, 800)
        self.assertEqual(f.getname()[0], "Libre Franklin")
        self.assertEqual(f.get_variation_axes()[0]["name"], b"Weight")
        original = g.FONT_PATH
        g.FONT_PATH = "/nonexistent/font.ttf"
        g._font.cache_clear()
        try:
            with self.assertRaises(g.FontUnavailable):
                g._font(104, 800)
        finally:
            g.FONT_PATH = original
            g._font.cache_clear()

    def test_constants_mirror_the_on_screen_css(self):
        self.assertIn("font-family: var(--font-sans)", CSS)
        self.assertIn("'Libre Franklin'", CSS)
        self.assertRegex(CSS, r"\.sk-value\{[^}]*font-weight: 400;[^}]*font-size: clamp\(14px, 4\.2vw, 26px\)[^}]*color: #2b5fb0")
        self.assertRegex(CSS, r"\.sk-cell\.sk-given \.sk-value\{ font-weight: 800; color: var\(--ink\)")
        self.assertRegex(CSS, r"\.sk-grid\{[^}]*max-width: 480px;[^}]*border: 3px solid var\(--black\); gap: 1px")
        self.assertIn("border-right: 3px solid var(--black)", CSS)
        self.assertEqual((g.SK_DIGIT, g.SK_THIN, g.SK_THICK), (26, 1, 3))
        self.assertEqual(g.USER_BLUE[:3], (0x2b, 0x5f, 0xb0))

    def _row_runs(self, im, y):
        runs, cur, start = [], False, 0
        for x in range(im.width):
            dark = im.getpixel((x, y))[3] > 0 and im.getpixel((x, y))[0] < 60
            if dark and not cur:
                cur, start = True, x
            elif not dark and cur:
                runs.append(x - start)
                cur = False
        return runs

    def test_thin_and_thick_grid_lines_keep_their_relative_thickness(self):
        im = _open(g.build_sudoku_png(SUDOKU))
        y = (g.SK_MARGIN + g.SK_TITLE_H + g.SK_THICK + 6) * 4   # inside the first cell row, above any digit
        self.assertEqual(self._row_runs(im, y), [12, 4, 4, 12, 4, 4, 12, 4, 4, 12])

    def test_digits_are_centred_large_and_bold_for_givens(self):
        im = _open(g.build_sudoku_png(SUDOKU))
        offs, _ = g._sk_offsets()
        left, top = g.SK_MARGIN, g.SK_MARGIN + g.SK_TITLE_H
        checked = 0
        for r in range(9):
            for c in range(9):
                if START[r][c] == 0:
                    continue
                x0, y0 = (left + offs[c]) * 4, (top + offs[r]) * 4
                cell = im.crop((x0, y0, x0 + g.SK_CELL * 4, y0 + g.SK_CELL * 4))
                ink = cell.point(lambda v: 255 if v < 100 else 0).convert("L") if False else None
                mask = Image.new("L", cell.size)
                mask.putdata([255 if (p[3] > 200 and p[0] < 100) else 0 for p in cell.getdata()])
                bb = mask.getbbox()
                self.assertIsNotNone(bb, (r, c))
                cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
                half = g.SK_CELL * 4 / 2
                self.assertLess(abs(cx - half), 14, f"digit at {r},{c} off-centre horizontally")   # digits like 1 are asymmetric
                self.assertLess(abs(cy - half), 8, f"digit at {r},{c} off-centre vertically")
                self.assertGreater(bb[3] - bb[1], g.SK_CELL * 4 * 0.33, "digit too small to read")
                checked += 1
        self.assertGreater(checked, 30)

    def test_solution_export_uses_blue_regular_weight_for_player_digits(self):
        im = _open(g.build_sudoku_png(SUDOKU, solved=True))
        offs, _ = g._sk_offsets()
        r, c = 0, 1  # empty in START, filled in SOLUTION
        x0 = (g.SK_MARGIN + offs[c]) * 4
        y0 = (g.SK_MARGIN + g.SK_TITLE_H + offs[r]) * 4
        cell = im.crop((x0, y0, x0 + g.SK_CELL * 4, y0 + g.SK_CELL * 4))
        self.assertTrue(any(p[3] > 240 and p[2] > p[0] + 80 for p in cell.getdata()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
