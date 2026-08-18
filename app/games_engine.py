"""Pure puzzle logic: crossword numbering/placement and sudoku solving/generation.

Kept independent of Flask/store so it can be unit-tested in isolation.
"""
import random


# ============================================================= crossword ==

def empty_grid(width, height):
    return [[{"block": False, "solution": ""} for _ in range(width)] for _ in range(height)]


def is_letter(grid, r, c):
    h = len(grid)
    w = len(grid[0]) if h else 0
    if r < 0 or r >= h or c < 0 or c >= w:
        return False
    return not grid[r][c]["block"]


def compute_slots(grid):
    """Derive crossword numbering + across/down word slots purely from block/letter
    layout, following standard newspaper-crossword numbering rules. Returns
    (numbers, slots) where numbers is a {(r,c): n} map and slots is a list of
    dicts: {number, direction, row, col, length, cells:[(r,c),...], answer}.
    """
    height = len(grid)
    width = len(grid[0]) if height else 0
    numbers = {}
    slots = []
    counter = 0

    for r in range(height):
        for c in range(width):
            if not is_letter(grid, r, c):
                continue
            starts_across = is_letter(grid, r, c) and not is_letter(grid, r, c - 1) and is_letter(grid, r, c + 1)
            starts_down = is_letter(grid, r, c) and not is_letter(grid, r - 1, c) and is_letter(grid, r + 1, c)
            if starts_across or starts_down:
                counter += 1
                numbers[(r, c)] = counter
                if starts_across:
                    cells = []
                    cc = c
                    while is_letter(grid, r, cc):
                        cells.append((r, cc))
                        cc += 1
                    answer = "".join(grid[rr][cc2]["solution"] or " " for rr, cc2 in cells)
                    slots.append({
                        "number": counter, "direction": "across", "row": r, "col": c,
                        "length": len(cells), "cells": cells, "answer": answer,
                    })
                if starts_down:
                    cells = []
                    rr = r
                    while is_letter(grid, rr, c):
                        cells.append((rr, c))
                        rr += 1
                    answer = "".join(grid[rr2][cc]["solution"] or " " for rr2, cc in cells)
                    slots.append({
                        "number": counter, "direction": "down", "row": r, "col": c,
                        "length": len(cells), "cells": cells, "answer": answer,
                    })
    return numbers, slots


def merge_clues(existing_clues, slots):
    """Reconcile admin-entered clue text with freshly recomputed slots: keeps
    clue text for slots that still exist (matched by number+direction+answer
    length), drops slots that no longer exist, adds empty-clue entries for new
    slots. Returns a new clues list ready to store alongside the grid.
    """
    existing_by_key = {(cl["number"], cl["direction"]): cl for cl in (existing_clues or [])}
    merged = []
    for slot in slots:
        key = (slot["number"], slot["direction"])
        prior = existing_by_key.get(key)
        clue_text = prior["clue"] if prior and prior.get("answer") == slot["answer"] else (prior["clue"] if prior else "")
        merged.append({
            "number": slot["number"],
            "direction": slot["direction"],
            "row": slot["row"],
            "col": slot["col"],
            "length": slot["length"],
            "answer": slot["answer"],
            "clue": clue_text,
        })
    return merged


def grid_conflicts(grid):
    """Sanity-check a hand-built grid for obviously broken structure: 1-letter
    islands (a letter cell with no across AND no down word of length >= 2
    running through it) which real crosswords never allow.
    """
    height = len(grid)
    width = len(grid[0]) if height else 0
    problems = []
    for r in range(height):
        for c in range(width):
            if not is_letter(grid, r, c):
                continue
            across_len = 1
            if is_letter(grid, r, c - 1) or is_letter(grid, r, c + 1):
                across_len = 2
            down_len = 1
            if is_letter(grid, r - 1, c) or is_letter(grid, r + 1, c):
                down_len = 2
            if across_len == 1 and down_len == 1:
                problems.append({"row": r, "col": c, "issue": "isolated_cell"})
    return problems


# --------------------------------------------------------- auto-generator --

def _place_word_mask(width, height):
    return [[None] * width for _ in range(height)]


def generate_crossword(entries, max_size=21):
    """Greedy crossword auto-placer. `entries` is a list of (answer, clue)
    pairs (answers already uppercased, letters only). Returns
    (grid, placements, unplaced) where grid is width/height + block/solution
    cells, placements is [{answer, clue, row, col, direction}], and unplaced
    is a list of answers that could not be fit in.
    """
    words = [(a.strip().upper(), clue) for a, clue in entries if a.strip()]
    words = [(a, c) for a, c in words if a.isalpha()]
    if not words:
        return empty_grid(5, 5), [], []
    words.sort(key=lambda wc: len(wc[0]), reverse=True)

    size = max_size
    mid = size // 2
    canvas = _place_word_mask(size, size)  # None = empty, else letter

    placements = []
    unplaced = []

    def fits(word, row, col, direction):
        for i, ch in enumerate(word):
            r = row + (i if direction == "down" else 0)
            c = col + (i if direction == "across" else 0)
            if not (0 <= r < size and 0 <= c < size):
                return False
            existing = canvas[r][c]
            if existing is not None and existing != ch:
                return False
        # cell immediately before/after the word must be empty (no accidental extension)
        if direction == "across":
            before_c, after_c = col - 1, col + len(word)
            if 0 <= before_c < size and canvas[row][before_c] is not None:
                return False
            if 0 <= after_c < size and canvas[row][after_c] is not None:
                return False
        else:
            before_r, after_r = row - 1, row + len(word)
            if 0 <= before_r < size and canvas[before_r][col] is not None:
                return False
            if 0 <= after_r < size and canvas[after_r][col] is not None:
                return False
        # cells adjacent (perpendicular) to non-intersection letters must be empty,
        # otherwise we'd silently create an unintended parallel word.
        for i, ch in enumerate(word):
            r = row + (i if direction == "down" else 0)
            c = col + (i if direction == "across" else 0)
            is_intersection = canvas[r][c] == ch
            if is_intersection:
                continue
            if direction == "across":
                for rr in (r - 1, r + 1):
                    if 0 <= rr < size and canvas[rr][c] is not None:
                        return False
            else:
                for cc in (c - 1, c + 1):
                    if 0 <= cc < size and canvas[r][cc] is not None:
                        return False
        return True

    def score(word, row, col, direction):
        crossings = 0
        for i, ch in enumerate(word):
            r = row + (i if direction == "down" else 0)
            c = col + (i if direction == "across" else 0)
            if canvas[r][c] == ch:
                crossings += 1
        center_dist = abs((row + (len(word) if direction == "down" else 1) / 2) - mid) + \
            abs((col + (len(word) if direction == "across" else 1) / 2) - mid)
        return (crossings, -center_dist)

    def place(word, row, col, direction):
        for i, ch in enumerate(word):
            r = row + (i if direction == "down" else 0)
            c = col + (i if direction == "across" else 0)
            canvas[r][c] = ch

    first_word, first_clue = words[0]
    place(first_word, mid, mid - len(first_word) // 2, "across")
    placements.append({"answer": first_word, "clue": first_clue, "row": mid, "col": mid - len(first_word) // 2, "direction": "across"})

    for word, clue in words[1:]:
        best = None
        for i, ch in enumerate(word):
            for r in range(size):
                for c in range(size):
                    if canvas[r][c] != ch:
                        continue
                    # try placing across through this intersection
                    candidates = [
                        (r, c - i, "across"),
                        (r - i, c, "down"),
                    ]
                    for row, col, direction in candidates:
                        if row < 0 or col < 0:
                            continue
                        if fits(word, row, col, direction):
                            s = score(word, row, col, direction)
                            if best is None or s > best[0]:
                                best = (s, row, col, direction)
        if best:
            _, row, col, direction = best
            place(word, row, col, direction)
            placements.append({"answer": word, "clue": clue, "row": row, "col": col, "direction": direction})
        else:
            unplaced.append(word)

    # trim to bounding box with 1-cell margin
    filled = [(r, c) for r in range(size) for c in range(size) if canvas[r][c] is not None]
    if not filled:
        return empty_grid(5, 5), [], [w for w, _ in words]
    min_r = max(min(r for r, c in filled) - 1, 0)
    max_r = min(max(r for r, c in filled) + 1, size - 1)
    min_c = max(min(c for r, c in filled) - 1, 0)
    max_c = min(max(c for r, c in filled) + 1, size - 1)

    out_h = max_r - min_r + 1
    out_w = max_c - min_c + 1
    grid = empty_grid(out_w, out_h)
    for r in range(out_h):
        for c in range(out_w):
            ch = canvas[min_r + r][min_c + c]
            if ch is None:
                grid[r][c] = {"block": True, "solution": ""}
            else:
                grid[r][c] = {"block": False, "solution": ch}

    for p in placements:
        p["row"] -= min_r
        p["col"] -= min_c

    return grid, placements, unplaced


# ================================================================ sudoku ==

BOX = 3
SIZE = 9


def _find_empty(grid):
    for r in range(SIZE):
        for c in range(SIZE):
            if grid[r][c] == 0:
                return r, c
    return None


def _candidates(grid, r, c):
    used = set(grid[r])
    used |= {grid[rr][c] for rr in range(SIZE)}
    br, bc = (r // BOX) * BOX, (c // BOX) * BOX
    for rr in range(br, br + BOX):
        for cc in range(bc, bc + BOX):
            used.add(grid[rr][cc])
    return [n for n in range(1, 10) if n not in used]


def is_valid_placement(grid, r, c, val):
    if val == 0:
        return True
    for cc in range(SIZE):
        if cc != c and grid[r][cc] == val:
            return False
    for rr in range(SIZE):
        if rr != r and grid[rr][c] == val:
            return False
    br, bc = (r // BOX) * BOX, (c // BOX) * BOX
    for rr in range(br, br + BOX):
        for cc in range(bc, bc + BOX):
            if (rr, cc) != (r, c) and grid[rr][cc] == val:
                return False
    return True


def find_conflicts(grid):
    """Return a set of (r, c) coordinates that violate row/col/box uniqueness,
    independent of any stored solution. Empty (0) cells are never conflicts."""
    bad = set()
    for r in range(SIZE):
        for c in range(SIZE):
            v = grid[r][c]
            if v == 0:
                continue
            if not is_valid_placement(grid, r, c, v):
                bad.add((r, c))
    return bad


def solve(grid, randomize=False):
    """Backtracking solver. Returns a solved copy, or None if unsolvable."""
    work = [row[:] for row in grid]

    def backtrack():
        spot = _find_empty(work)
        if spot is None:
            return True
        r, c = spot
        cands = _candidates(work, r, c)
        if randomize:
            random.shuffle(cands)
        for val in cands:
            work[r][c] = val
            if backtrack():
                return True
            work[r][c] = 0
        return False

    return work if backtrack() else None


def count_solutions(grid, limit=2):
    """Count solutions up to `limit` (stops early once reached) -- used to
    verify a dug puzzle still has a UNIQUE solution without paying the cost
    of enumerating every solution."""
    work = [row[:] for row in grid]
    count = 0

    def backtrack():
        nonlocal count
        if count >= limit:
            return
        spot = _find_empty(work)
        if spot is None:
            count += 1
            return
        r, c = spot
        for val in _candidates(work, r, c):
            work[r][c] = val
            backtrack()
            work[r][c] = 0
            if count >= limit:
                return

    backtrack()
    return count


def generate_solution():
    empty = [[0] * SIZE for _ in range(SIZE)]
    return solve(empty, randomize=True)


DIFFICULTY_TARGET_GIVENS = {
    "easy": 40,
    "medium": 32,
    "hard": 27,
    "expert": 23,
}


def dig_holes(solution, difficulty):
    """Remove digits from a full solution while checking, after every single
    removal, that the puzzle still has exactly one solution -- never removes
    blindly. Runs a couple of shuffled passes to get as close as possible to
    the difficulty's target given-count."""
    target = DIFFICULTY_TARGET_GIVENS.get(difficulty, 30)
    puzzle = [row[:] for row in solution]
    positions = [(r, c) for r in range(SIZE) for c in range(SIZE)]

    def givens_count():
        return sum(1 for r in range(SIZE) for c in range(SIZE) if puzzle[r][c] != 0)

    for _ in range(3):
        if givens_count() <= target:
            break
        random.shuffle(positions)
        for r, c in positions:
            if givens_count() <= target:
                break
            if puzzle[r][c] == 0:
                continue
            saved = puzzle[r][c]
            puzzle[r][c] = 0
            if count_solutions(puzzle, limit=2) != 1:
                puzzle[r][c] = saved
    return puzzle


def generate_sudoku(difficulty):
    solution = generate_solution()
    puzzle = dig_holes(solution, difficulty)
    return puzzle, solution


def validate_manual_sudoku(starting_grid):
    """For the manual admin editor: check the entered givens are internally
    consistent and determine solvability/uniqueness. Returns a dict with
    ok/solution/solution_count(capped)/conflicts."""
    conflicts = find_conflicts(starting_grid)
    if conflicts:
        return {"ok": False, "reason": "conflict", "conflicts": list(conflicts), "solution": None, "solution_count": 0}
    n = count_solutions(starting_grid, limit=2)
    if n == 0:
        return {"ok": False, "reason": "no_solution", "conflicts": [], "solution": None, "solution_count": 0}
    if n > 1:
        return {"ok": False, "reason": "multiple_solutions", "conflicts": [], "solution": None, "solution_count": n}
    solution = solve(starting_grid)
    return {"ok": True, "reason": None, "conflicts": [], "solution": solution, "solution_count": 1}
