#!/usr/bin/env python3
"""Verify Hangul coverage and Fixedsys invariants in built font files."""

from __future__ import annotations

import argparse
from pathlib import Path

from fontTools.ttLib import TTFont

from merge_hangul import is_hangul


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def verify_font(font_path: Path, donor: TTFont, expected_hangul: set[int]) -> bytes:
    font = TTFont(font_path)
    try:
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

        hinted_hangul = []
        for codepoint in actual_hangul:
            glyph = font["glyf"][cmap[codepoint]]
            program = getattr(glyph, "program", None)
            if program is not None and program.getBytecode():
                hinted_hangul.append(codepoint)
        require(not hinted_hangul, f"{font_path}: donor hinting remains in Hangul glyphs")

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
