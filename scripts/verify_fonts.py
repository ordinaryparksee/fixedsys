#!/usr/bin/env python3
"""Verify Hangul coverage and Fixedsys invariants in built font files."""

from __future__ import annotations

import argparse
from pathlib import Path

from fontTools.ttLib import TTFont

from merge_hangul import is_hangul
from hint_pixels import PRE_PROGRAM, font_program, glyph_program, hint_plans, phase_catalog, program


RELEASE_VERSION = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def verify_font(font_path: Path, donor: TTFont, expected_hangul: set[int]) -> bytes:
    font = TTFont(font_path)
    try:
        for record in font["name"].names:
            if record.nameID == 3:
                require(f":{RELEASE_VERSION}:" in record.toUnicode(),
                        f"{font_path}: outdated unique identifier")
            elif record.nameID == 5:
                require(f"release v{RELEASE_VERSION};" in record.toUnicode(),
                        f"{font_path}: outdated release version")
        cmap = font.getBestCmap() or {}
        actual_hangul = {codepoint for codepoint in cmap if is_hangul(codepoint)}
        missing = expected_hangul - actual_hangul
        unexpected = actual_hangul - expected_hangul
        require(not missing, f"{font_path}: missing {len(missing)} Hangul mappings")
        require(not unexpected, f"{font_path}: has {len(unexpected)} unexpected Hangul mappings")

        require(font["head"].unitsPerEm == donor["head"].unitsPerEm, f"{font_path}: unitsPerEm mismatch")
        require(font["hhea"].ascent == donor["hhea"].ascent, f"{font_path}: ascent mismatch")
        require(font["hhea"].descent == donor["hhea"].descent, f"{font_path}: descent mismatch")
        require(font["hhea"].lineGap == donor["hhea"].lineGap, f"{font_path}: lineGap mismatch")

        space_width = font["hmtx"][cmap[0x20]][0]
        wrong_widths = {
            font["hmtx"][cmap[codepoint]][0]
            for codepoint in actual_hangul
            if font["hmtx"][cmap[codepoint]][0] != space_width * 2
        }
        require(not wrong_widths, f"{font_path}: unexpected Hangul widths {sorted(wrong_widths)}")

        require("fpgm" in font and "prep" in font, f"{font_path}: missing pixel hinting tables")
        plans = hint_plans(font)
        catalog = phase_catalog(plans)
        require(font["fpgm"].program.getBytecode() == font_program(catalog).getBytecode(),
                f"{font_path}: unexpected font program")
        require(font["prep"].program.getBytecode() == program(PRE_PROGRAM).getBytecode(),
                f"{font_path}: unexpected pre-program")
        require(font["maxp"].maxFunctionDefs >= len(catalog) + 2,
                f"{font_path}: missing hint function capacity")
        require(font["maxp"].maxStackElements >= 19, f"{font_path}: insufficient hinting stack")
        require(font["maxp"].maxStorage >= 1, f"{font_path}: insufficient phase storage")
        max_instructions = 0
        for name in font.getGlyphOrder():
            glyph = font["glyf"][name]
            instructions = getattr(glyph, "program", None)
            bytecode = instructions.getBytecode() if instructions is not None else b""
            expected = b""
            if name in plans:
                points, x_phases, y_phases = plans[name]
                expected = glyph_program(points, catalog[x_phases], catalog[y_phases]).getBytecode()
            require(bytecode == expected, f"{font_path}: unexpected or missing hinting for {name}")
            max_instructions = max(max_instructions, len(bytecode))
        require(font["maxp"].maxSizeOfInstructions >= max_instructions,
                f"{font_path}: insufficient glyph instruction capacity")
        require(font["gasp"].version == 1 and font["gasp"].gaspRange == {8: 2, 16: 0, 65535: 15},
                f"{font_path}: unexpected grid-fitting policy")

        format_4 = [
            table
            for table in font["cmap"].tables
            if table.platformID == 3 and table.platEncID == 1 and table.format == 4
        ]
        format_12 = [
            table
            for table in font["cmap"].tables
            if table.platformID == 3 and table.platEncID == 10 and table.format == 12
        ]
        require(len(format_4) == 1, f"{font_path}: expected one Windows format 4 cmap")
        require(len(format_12) == 1, f"{font_path}: expected one Windows format 12 cmap")
        require(0x1F512 not in format_4[0].cmap, f"{font_path}: U+1F512 leaked into format 4")
        require(cmap.get(0x1F512) == "u01F512", f"{font_path}: U+1F512 is not mapped")
        require(format_12[0].cmap == cmap, f"{font_path}: format 12 is not the complete cmap")
        require(font["OS/2"].usLastCharIndex == 0xFFFF, f"{font_path}: invalid OS/2 last char")
        require("GSUB" in font, f"{font_path}: missing programming ligatures")

        family = font["name"].getDebugName(1) or "unknown"
        print(
            f"Verified {font_path}: family={family!r}, glyphs={font['maxp'].numGlyphs}, "
            f"cmap={len(cmap)}, hangul={len(actual_hangul)}, hangul_width={space_width * 2}"
        )
        return font.getTableData("GSUB")
    finally:
        font.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("donor", type=Path, help="TTF containing the source Hangul glyphs")
    parser.add_argument("fonts", type=Path, nargs="+", help="built Fixedsys TTF files")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    donor = TTFont(args.donor)
    try:
        donor_cmap = donor.getBestCmap() or {}
        expected_hangul = {codepoint for codepoint in donor_cmap if is_hangul(codepoint)}
        require(expected_hangul, f"{args.donor}: no Hangul mappings")
        gsub_tables = [verify_font(path, donor, expected_hangul) for path in args.fonts]
    finally:
        donor.close()

    if len(gsub_tables) > 1:
        require(len(set(gsub_tables)) > 1, "default and ALT fonts have identical GSUB tables")


if __name__ == "__main__":
    main()
