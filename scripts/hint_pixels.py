#!/usr/bin/env python3
"""Add size-dependent TrueType grid fitting to Fixedsys pixel outlines."""

from __future__ import annotations

import argparse
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables.ttProgram import Program


# Keep the native 16px design untouched, including a few fine-detail symbols
# whose coordinates deliberately sit between the main 10-unit pixel grid.
MIN_PPEM = 17

# Function 0 consumes a point index and leaves the next index on the stack.
# Explicit F26Dot6 arithmetic rounds to physical pixels: ClearType's MDAP
# rounding may otherwise use a finer horizontal grid. FLOOR also handles
# negative coordinates without truncating them toward zero.
FONT_PROGRAM = (
    "PUSHB[ ]", "0", "FDEF[ ]",
    "DUP[ ]", "DUP[ ]", "GC[0]", "PUSHB[ ]", "0", "RS[ ]", "ADD[ ]",
    "FLOOR[ ]", "SCFS[ ]", "PUSHB[ ]", "1", "ADD[ ]", "ENDF[ ]",
    # Function 1 receives a residue followed by eight packed bytes (two
    # four-bit phases per byte). Lookup constants are not CVT values, so
    # the interpreter must not scale them with the current PPEM.
    "PUSHB[ ]", "1", "FDEF[ ]",
    "PUSHB[ ]", "9", "CINDEX[ ]", "PUSHB[ ]", "2", "DIV[ ]",
    "DUP[ ]", "FLOOR[ ]", "DUP[ ]", "PUSHB[ ]", "1", "MUL[ ]",
    "PUSHB[ ]", "10", "SWAP[ ]", "SUB[ ]", "CINDEX[ ]",
    # Stack tail: residue/2 (F26Dot6), floor(residue/2), packed byte.
    "ROLL[ ]", "ROLL[ ]", "SUB[ ]", "IF[ ]",
    "PUSHB[ ]", "16", "DIV[ ]", "FLOOR[ ]", "PUSHB[ ]", "1", "MUL[ ]", "EIF[ ]",
    # Extract the low nibble and convert quarter-sixteenths back to /64.
    "DUP[ ]", "PUSHB[ ]", "16", "DIV[ ]", "FLOOR[ ]",
    "PUSHB[ ]", "16", "MUL[ ]", "SUB[ ]", "PUSHW[ ]", "256", "MUL[ ]",
    "PUSHB[ ]", "0", "SWAP[ ]", "WS[ ]",
    *("POP[ ]",) * 9,
    "ENDF[ ]",
)

# FreeType's modern interpreter can ignore horizontal moves in compatibility
# mode. Opt into native instructions on rasterizers that support selector 3.
# Disable instructions for rotation/non-uniform scaling rather than snapping
# a transformed pixel design to the wrong axes.
PRE_PROGRAM = (
    "MPPEM[ ]", "PUSHB[ ]", str(MIN_PPEM), "GTEQ[ ]", "IF[ ]",
    "PUSHB[ ]", "1", "GETINFO[ ]", "PUSHB[ ]", "37", "GTEQ[ ]", "IF[ ]",
    "PUSHB[ ]", "4", "3", "INSTCTRL[ ]", "EIF[ ]", "EIF[ ]",
    "PUSHB[ ]", "6", "GETINFO[ ]", "IF[ ]",
    "PUSHB[ ]", "1", "1", "INSTCTRL[ ]", "EIF[ ]",
)


def program(assembly: tuple[str, ...] | list[str]) -> Program:
    result = Program()
    result.fromAssembly(list(assembly))
    return result


def pixel_point_count(glyph: object, glyf: object) -> int:
    """Return a point count for outlines on Fixedsys's native 10-unit grid."""
    if glyph.numberOfContours <= 0:
        return 0
    coordinates, ends, flags = glyph.getCoordinates(glyf)
    if not all(flag & 1 for flag in flags):
        return 0
    # A few rectilinear symbols use finer coordinates intentionally. Applying
    # pixel-font rounding to those would damage their fine detail.
    if any(x % 10 or y % 10 for x, y in coordinates):
        return 0
    start = 0
    for end in ends:
        contour = coordinates[start : end + 1]
        for (x1, y1), (x2, y2) in zip(contour, contour[1:] + contour[:1]):
            if x1 != x2 and y1 != y2:
                return 0
        start = end + 1
    return len(coordinates)


def stem_spans(glyph: object, glyf: object, axis: int) -> tuple[tuple[int, int, int], ...]:
    """Measure narrow filled spans, weighted by opposing-edge overlap length.

    Work in native pixel cells. Non-zero winding measures filled runs across
    overlapping contours; wide filled areas are not mistaken for narrow stems.
    """
    coordinates, ends, _ = glyph.getCoordinates(glyf)
    levels = sorted({point[1 - axis] // 10 for point in coordinates})
    edges = []
    start = 0
    for end in ends:
        contour = coordinates[start : end + 1]
        for a, b in zip(contour, contour[1:] + contour[:1]):
            if a[axis] == b[axis] and a[1 - axis] != b[1 - axis]:
                low, high = sorted((a[1 - axis] // 10, b[1 - axis] // 10))
                edges.append((a[axis] // 10, low, high, 1 if b[1 - axis] > a[1 - axis] else -1))
        start = end + 1
    spans = defaultdict(int)
    for low, high in zip(levels, levels[1:]):
        crossings = defaultdict(int)
        for position, begin, end, direction in edges:
            if begin <= low and end >= high:
                crossings[position] += direction
        winding = 0
        begin = 0
        for position, direction in sorted(crossings.items()):
            was_filled = bool(winding)
            winding += direction
            if not was_filled and winding:
                begin = position
            elif was_filled and not winding and 0 < position - begin <= 2:
                spans[begin, position] += high - low
    return tuple((a, b, weight) for (a, b), weight in sorted(spans.items()))


@lru_cache(maxsize=None)
def stem_phases(spans: tuple[tuple[int, int, int], ...]) -> tuple[int, ...]:
    """Choose rounding phases that minimize paired stem-width errors.

    A native cell is 1/16 em. Its fractional position repeats every 16 PPEM,
    so a 16-entry profile covers all integer sizes without per-size outlines.
    Long stems take priority over short pixel steps. Ties retain the old
    nearest-pixel phase (32/64) as closely as possible.
    """
    result = []
    for residue in range(16):
        def error(phase: int) -> tuple[int, int, int]:
            cost = 0
            for left, right, overlap in spans:
                actual = (right * residue * 4 + phase) // 64 - (left * residue * 4 + phase) // 64
                target = ((right - left) * residue + 8) // 16
                cost += overlap * overlap * (actual - target) ** 2
            return cost, abs(phase - 32), phase

        result.append(min(range(0, 64, 4), key=error))
    return tuple(result)


def hint_plans(font: TTFont) -> dict[str, tuple[int, tuple[int, ...], tuple[int, ...]]]:
    if font["head"].unitsPerEm != 160:
        raise ValueError("Fixedsys pixel hinting requires 160 units per em")
    plans = {}
    glyf = font["glyf"]
    for name in font.getGlyphOrder():
        glyph = glyf[name]
        count = pixel_point_count(glyph, glyf)
        if count:
            plans[name] = (
                count,
                stem_phases(stem_spans(glyph, glyf, 0)),
                stem_phases(stem_spans(glyph, glyf, 1)),
            )
    return plans


def phase_catalog(plans: dict) -> dict[tuple[int, ...], int]:
    profiles = sorted({profile for _, x, y in plans.values() for profile in (x, y)})
    if len(profiles) > 32766:
        raise ValueError("too many stem profiles for signed TrueType function indices")
    return {profile: index + 2 for index, profile in enumerate(profiles)}


def push(*values: int) -> list[str]:
    kind = "B" if all(0 <= value <= 255 for value in values) else "W"
    opcode = ("NPUSH" if len(values) > 8 else "PUSH") + kind + "[ ]"
    return [opcode, *(str(value) for value in values)]


def font_program(catalog: dict[tuple[int, ...], int]) -> Program:
    assembly = list(FONT_PROGRAM)
    for phases, function in catalog.items():
        assembly.extend(push(function) + ["FDEF[ ]"])
        packed = tuple(phases[i] // 4 + (phases[i + 1] // 4) * 16 for i in range(0, 16, 2))
        assembly.extend(push(*packed) + push(1) + ["CALL[ ]", "ENDF[ ]"])
    result = program(assembly)
    # OTS rejects fpgm tables at or above 128 KiB, even when FreeType accepts
    # them. Keep webfonts within that limit instead of producing a font that
    # works when installed but fails to load through @font-face.
    if len(result.getBytecode()) >= 128 * 1024:
        raise ValueError("stem profiles exceed the browser's 128 KiB font-program limit")
    return result


def glyph_program(point_count: int, x_function: int, y_function: int) -> Program:
    if not 0 < point_count <= 32767:
        raise ValueError(f"invalid LOOPCALL point count: {point_count}")
    assembly = ["MPPEM[ ]", "PUSHB[ ]", str(MIN_PPEM), "GTEQ[ ]", "IF[ ]"]
    for axis, function in ((1, x_function), (0, y_function)):
        # Compute PPEM % 16 using F26Dot6 DIV/FLOOR/MUL, then select this
        # glyph's phase for the entire axis. One monotone map preserves gaps.
        assembly.extend(
            ["MPPEM[ ]", "DUP[ ]", *push(16), "DIV[ ]", "FLOOR[ ]",
             *push(16), "MUL[ ]", "SUB[ ]", *push(function), "CALL[ ]"]
        )
        assembly.extend(
            [f"SVTCA[{axis}]", *push(0, point_count, 0), "LOOPCALL[ ]", "POP[ ]"]
        )
    assembly.append("EIF[ ]")
    return program(assembly)


def add_pixel_hints(font: TTFont) -> int:
    # The merge stage has already removed donor instructions. Reject a font
    # with a separate hinting system instead of silently breaking its glyphs.
    if any(tag in font for tag in ("fpgm", "prep", "cvt ")):
        raise ValueError("pixel hinting requires a font without global hinting tables")
    glyf = font["glyf"]
    for name in font.getGlyphOrder():
        existing = getattr(glyf[name], "program", None)
        if existing is not None and existing.getBytecode():
            raise ValueError(f"pixel hinting requires unhinted input: {name}")

    plans = hint_plans(font)
    catalog = phase_catalog(plans)
    max_instructions = 0
    for name, (points, x_phases, y_phases) in plans.items():
        glyph = glyf[name]
        glyph.program = glyph_program(points, catalog[x_phases], catalog[y_phases])
        max_instructions = max(max_instructions, len(glyph.program.getBytecode()))
    if not plans:
        raise ValueError("font contains no supported pixel outlines")

    font["fpgm"] = newTable("fpgm")
    font["fpgm"].program = font_program(catalog)
    font["prep"] = newTable("prep")
    font["prep"].program = program(PRE_PROGRAM)
    font["maxp"].maxFunctionDefs = len(catalog) + 2
    font["maxp"].maxStackElements = 19
    font["maxp"].maxStorage = 1
    font["maxp"].maxSizeOfInstructions = max_instructions

    # Preserve the original small-size policy; request grid fitting for both
    # grayscale and symmetric ClearType renderers above the native 16px size.
    font["gasp"].version = 1
    font["gasp"].gaspRange = {8: 2, 16: 0, 65535: 15}
    return len(plans)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="merged, unhinted Fixedsys TTF")
    parser.add_argument("output", type=Path, help="output TTF")
    args = parser.parse_args()
    with TTFont(args.input) as font:
        count = add_pixel_hints(font)
        font.save(args.output)
    print(f"Added pixel hints to {count} glyphs in {args.output} (from {MIN_PPEM} PPEM)")


if __name__ == "__main__":
    main()
