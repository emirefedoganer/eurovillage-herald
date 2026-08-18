"""Render crossword/sudoku puzzles to print-ready PNGs for the admin panel,
so a finished puzzle can be dropped straight into the next paper issue."""
import io
import os

from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES_BOLD = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_FONT_CANDIDATES_REGULAR = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size, bold=False):
    for path in (_FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REGULAR):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def build_crossword_png(crossword, cell_size=48):
    grid = crossword["grid"]
    height = len(grid)
    width = len(grid[0]) if height else 0
    margin = 24
    title_h = cell_size
    img_w = width * cell_size + margin * 2
    img_h = height * cell_size + margin * 2 + title_h

    img = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)

    from games_engine import compute_slots
    numbers, _slots = compute_slots(grid)

    number_font = _font(max(10, cell_size // 4))
    title_font = _font(max(14, cell_size // 3), bold=True)

    draw.text((margin, 4), crossword.get("title", ""), fill="black", font=title_font)

    grid_top = margin + title_h
    for r in range(height):
        for c in range(width):
            x0 = margin + c * cell_size
            y0 = grid_top + r * cell_size
            x1, y1 = x0 + cell_size, y0 + cell_size
            cell = grid[r][c]
            if cell["block"]:
                draw.rectangle([x0, y0, x1, y1], fill="black")
            else:
                draw.rectangle([x0, y0, x1, y1], outline="black", width=2, fill="white")
                num = numbers.get((r, c))
                if num:
                    draw.text((x0 + 3, y0 + 1), str(num), fill="black", font=number_font)

    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out


def build_sudoku_png(sudoku, cell_size=56, solved=False):
    grid = sudoku["solution_grid"] if solved else sudoku["starting_grid"]
    given_grid = sudoku["starting_grid"]
    margin = 30
    size = 9 * cell_size
    img_w = size + margin * 2
    img_h = size + margin * 2 + 36

    img = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)

    title_font = _font(max(14, cell_size // 3), bold=True)
    num_font = _font(int(cell_size * 0.55), bold=True)
    num_font_user = _font(int(cell_size * 0.5), bold=False)

    draw.text((margin, 4), sudoku.get("title", ""), fill="black", font=title_font)

    top = margin + 36
    for i in range(10):
        w = 4 if i % 3 == 0 else 1
        x = margin + i * cell_size
        draw.line([(x, top), (x, top + size)], fill="black", width=w)
        y = top + i * cell_size
        draw.line([(margin, y), (margin + size, y)], fill="black", width=w)

    for r in range(9):
        for c in range(9):
            val = grid[r][c]
            if not val:
                continue
            is_given = given_grid[r][c] != 0
            font = num_font if is_given else num_font_user
            x = margin + c * cell_size
            y = top + r * cell_size
            bbox = draw.textbbox((0, 0), str(val), font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text((x + (cell_size - tw) / 2, y + (cell_size - th) / 2 - bbox[1]), str(val), fill="black", font=font)

    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out
