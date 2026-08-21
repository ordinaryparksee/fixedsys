#!/usr/bin/env python3
"""Copy only Hangul glyphs from a donor TrueType font into Fixedsys."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from fontTools.ttLib.tables.O_S_2f_2 import (
    calcCodePageRanges,
    intersectUnicodeRanges,
)


HANGUL_RANGES = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
    (0xA960, 0xA97F),  # Hangul Jamo Extended-A
    (0xAC00, 0xD7A3),  # Hangul Syllables
    (0xD7B0, 0xD7FF),  # Hangul Jamo Extended-B
    (0xFFA0, 0xFFDC),  # Halfwidth Hangul
)

REQUIRED_TABLES = {"head", "hhea", "hmtx", "maxp", "cmap", "glyf", "OS/2"}
SUPPLEMENTARY_MAPPINGS = {0x1F512: "u01F512"}


def is_hangul(codepoint: int) -> bool:
    return any(start <= codepoint <= end for start, end in HANGUL_RANGES)


def require_tables(font: TTFont, label: str) -> None:
    missing = sorted(REQUIRED_TABLES - set(font.keys()))
    if missing:
        raise ValueError(f"{label} is missing required tables: {', '.join(missing)}")


def require_compatible_metrics(base: TTFont, donor: TTFont) -> None:
    checks = (
        ("unitsPerEm", base["head"].unitsPerEm, donor["head"].unitsPerEm),
        ("ascent", base["hhea"].ascent, donor["hhea"].ascent),
        ("descent", base["hhea"].descent, donor["hhea"].descent),
        ("lineGap", base["hhea"].lineGap, donor["hhea"].lineGap),
    )
    mismatches = [
        f"{name}: base={base_value}, donor={donor_value}"
        for name, base_value, donor_value in checks
        if base_value != donor_value
    ]
    if mismatches:
        raise ValueError("incompatible font metrics: " + "; ".join(mismatches))


def set_os2_bits(os2_table: object, prefix: str, bits: set[int]) -> None:
    for bit in bits:
        field_number = bit // 32 + 1
        field_name = f"{prefix}{field_number}"
        current = getattr(os2_table, field_name)
        setattr(os2_table, field_name, current | (1 << (bit % 32)))


def merge_hangul(base_path: Path, donor_path: Path, output_path: Path) -> int:
    base = TTFont(base_path)
    donor = TTFont(donor_path)

    try:
        require_tables(base, "base font")
        require_tables(donor, "donor font")
        require_compatible_metrics(base, donor)

        donor_cmap = donor.getBestCmap() or {}
        selected = {
            codepoint: glyph_name
            for codepoint, glyph_name in sorted(donor_cmap.items())
            if is_hangul(codepoint)
        }
        if not selected:
            raise ValueError("donor font has no mapped Hangul glyphs")

        base_cmap: dict[int, str] = {}
        for cmap_table in base["cmap"].tables:
            if cmap_table.isUnicode():
                base_cmap.update(cmap_table.cmap)
        codepoint_collisions = sorted(set(base_cmap) & set(selected))
        if codepoint_collisions:
            sample = ", ".join(f"U+{value:04X}" for value in codepoint_collisions[:8])
            raise ValueError(f"base font already maps Hangul codepoints: {sample}")

        selected_names = list(dict.fromkeys(selected.values()))
        base_names = set(base.getGlyphOrder())
        name_collisions = sorted(base_names & set(selected_names))
        if name_collisions:
            raise ValueError(
                "base font already contains donor glyph names: "
                + ", ".join(name_collisions[:8])
            )

        donor_glyf = donor["glyf"]
        component_dependencies = {
            component.glyphName
            for glyph_name in selected_names
            if donor_glyf[glyph_name].isComposite()
            for component in donor_glyf[glyph_name].components
        }
        missing_dependencies = sorted(component_dependencies - set(selected_names))
        if missing_dependencies:
            raise ValueError(
                "Hangul glyphs depend on non-Hangul components: "
                + ", ".join(missing_dependencies[:8])
            )

        hangul_widths = {donor["hmtx"][name][0] for name in selected_names}
        base_width = base["hmtx"][base_cmap[0x20]][0]
        if hangul_widths != {base_width * 2}:
            raise ValueError(
                f"expected double-width Hangul ({base_width * 2}), "
                f"found {sorted(hangul_widths)}"
            )

        for glyph_name in selected_names:
            glyph = deepcopy(donor_glyf[glyph_name])
            # Donor instructions refer to donor-specific cvt/fpgm/prep tables.
            # Pixel-grid-aligned outlines remain crisp without those instructions.
            glyph.removeHinting()
            base["glyf"].glyphs[glyph_name] = glyph
            base["hmtx"].metrics[glyph_name] = donor["hmtx"].metrics[glyph_name]

        base.setGlyphOrder(base.getGlyphOrder() + selected_names)
        unicode_cmaps = [table for table in base["cmap"].tables if table.isUnicode()]
        if not unicode_cmaps:
            raise ValueError("base font has no Unicode cmap table")
        for cmap_table in unicode_cmaps:
            cmap_table.cmap.update(selected)

        full_unicode_cmap = dict(base_cmap)
        full_unicode_cmap.update(selected)
        for codepoint, glyph_name in SUPPLEMENTARY_MAPPINGS.items():
            if glyph_name in base_names:
                full_unicode_cmap[codepoint] = glyph_name

        format_12_tables = [
            table
            for table in base["cmap"].tables
            if table.platformID == 3 and table.platEncID == 10 and table.format == 12
        ]
        if not format_12_tables:
            format_12 = CmapSubtable.newSubtable(12)
            format_12.platformID = 3
            format_12.platEncID = 10
            format_12.language = 0
            base["cmap"].tables.append(format_12)
            format_12_tables = [format_12]
        for cmap_table in format_12_tables:
            cmap_table.cmap = dict(full_unicode_cmap)

        final_codepoints = set(full_unicode_cmap)
        set_os2_bits(
            base["OS/2"],
            "ulUnicodeRange",
            intersectUnicodeRanges(selected),
        )
        set_os2_bits(
            base["OS/2"],
            "ulCodePageRange",
            calcCodePageRanges(final_codepoints),
        )
        base["OS/2"].usFirstCharIndex = min(final_codepoints)
        base["OS/2"].usLastCharIndex = (
            0xFFFF
            if any(codepoint > 0xFFFF for codepoint in final_codepoints)
            else max(final_codepoints)
        )

        base.save(output_path)
    finally:
        base.close()
        donor.close()

    merged = TTFont(output_path)
    try:
        merged_cmap = merged.getBestCmap() or {}
        missing = sorted(set(selected) - set(merged_cmap))
        if missing:
            sample = ", ".join(f"U+{value:04X}" for value in missing[:8])
            raise ValueError(f"saved font is missing Hangul mappings: {sample}")
        wrong_widths = [
            codepoint
            for codepoint in selected
            if merged["hmtx"][merged_cmap[codepoint]][0] != base_width * 2
        ]
        if wrong_widths:
            raise ValueError(f"saved font has {len(wrong_widths)} wrong Hangul widths")
    finally:
        merged.close()

    return len(selected)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path, help="compiled Fixedsys TTF")
    parser.add_argument("donor", type=Path, help="TTF containing Hangul glyphs")
    parser.add_argument("output", type=Path, help="merged output TTF")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    count = merge_hangul(args.base, args.donor, args.output)
    print(f"Merged {count} Hangul mappings into {args.output}")


if __name__ == "__main__":
    main()
