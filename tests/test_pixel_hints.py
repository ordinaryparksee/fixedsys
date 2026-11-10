"""Exercise generated instructions in both legacy and modern FreeType engines."""

from __future__ import annotations

import ctypes
import sys
import tempfile
import unittest
from pathlib import Path

import freetype
from fontTools.ttLib import TTFont
from fontTools.pens.ttGlyphPen import TTGlyphPen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from hint_pixels import add_pixel_hints, pixel_point_count, stem_phases, stem_spans


SIZES = (16, 18, 20, 21, 22, 24, 28, 32, 48)
SAMPLE = "$0123456789.가나다라마바사아자차카타파하한글금액사용한도ABCxyz"
FLAGS = freetype.FT_LOAD_NO_BITMAP | freetype.FT_LOAD_NO_AUTOHINT | freetype.FT_LOAD_PEDANTIC


def set_interpreter(version: int) -> None:
    value = ctypes.c_uint(version)
    error = freetype.FT_Property_Set(
        freetype.get_handle(), b"truetype", b"interpreter-version", ctypes.byref(value)
    )
    if error:
        raise RuntimeError(f"cannot select FreeType interpreter {version}: {error}")


class PixelHintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.font = TTFont(ROOT / "FixedsysEX.ttf")
        cls.temp = tempfile.TemporaryDirectory()
        cls.unhinted_path = Path(cls.temp.name) / "unhinted.ttf"
        with TTFont(ROOT / "FixedsysEX.ttf") as font:
            for name in font.getGlyphOrder():
                font["glyf"][name].removeHinting()
            for tag in ("fpgm", "prep"):
                del font[tag]
            font["maxp"].maxSizeOfInstructions = 0
            font["gasp"].version = 0
            font["gasp"].gaspRange = {8: 2, 16: 0, 65535: 3}
            font.save(cls.unhinted_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.font.close()
        cls.temp.cleanup()

    def tearDown(self) -> None:
        set_interpreter(40)

    def test_adding_hints_preserves_outlines_metrics_and_shaping(self) -> None:
        with TTFont(self.unhinted_path) as font:
            before_glyphs = {
                name: font["glyf"][name].getCoordinates(font["glyf"])
                for name in font.getGlyphOrder()
            }
            metrics = font.getTableData("hmtx")
            gsub = font.getTableData("GSUB")
            cmap = font.getBestCmap().copy()
            self.assertEqual(add_pixel_hints(font), 17686)
            self.assertEqual(font.getTableData("hmtx"), metrics)
            self.assertEqual(font.getTableData("GSUB"), gsub)
            self.assertEqual(font.getBestCmap(), cmap)
            for name, coordinates in before_glyphs.items():
                self.assertEqual(font["glyf"][name].getCoordinates(font["glyf"]), coordinates, name)

    def test_existing_hint_system_is_rejected(self) -> None:
        with TTFont(ROOT / "FixedsysEX.ttf") as font:
            with self.assertRaisesRegex(ValueError, "global hinting"):
                add_pixel_hints(font)

    def test_stem_detection_respects_overlaps_and_counters(self) -> None:
        pen = TTGlyphPen(None)
        for left, right in ((10, 30), (20, 40)):
            pen.moveTo((left, 0))
            for point in ((left, 40), (right, 40), (right, 0)):
                pen.lineTo(point)
            pen.closePath()
        # Their union is 30 units wide; internal overlapping edges must not
        # be classified as separate 10-unit stems.
        self.assertEqual(stem_spans(pen.glyph(), None, 0), ())

        pen = TTGlyphPen(None)
        for contour in (((0, 0), (0, 40), (40, 40), (40, 0)),
                        ((10, 10), (30, 10), (30, 30), (10, 30))):
            pen.moveTo(contour[0])
            for point in contour[1:]:
                pen.lineTo(point)
            pen.closePath()
        self.assertEqual(stem_spans(pen.glyph(), None, 0), ((0, 1, 2), (3, 4, 2)))

    def test_every_pixel_glyph_executes_and_snaps_to_the_grid(self) -> None:
        names = [
            name for name in self.font.getGlyphOrder()
            if pixel_point_count(self.font["glyf"][name], self.font["glyf"])
        ]
        for version in (35, 40):
            set_interpreter(version)
            face = freetype.Face(str(ROOT / "FixedsysEX.ttf"))
            for size in (18, 21, 22, 32):
                face.set_pixel_sizes(0, size)
                for name in names:
                    face.load_glyph(self.font.getGlyphID(name), FLAGS)
                    self.assertTrue(
                        all(x % 64 == 0 and y % 64 == 0 for x, y in face.glyph.outline.points),
                        f"off-grid outline: interpreter={version}, size={size}, glyph={name}",
                    )
                    if size == 21:
                        original, _, _ = self.font["glyf"][name].getCoordinates(self.font["glyf"])
                        fitted = face.glyph.outline.points
                        for axis in (0, 1):
                            mapping = {}
                            for before, after in zip(original, fitted):
                                self.assertEqual(mapping.setdefault(before[axis], after[axis]), after[axis])
                            positions = [mapping[value] for value in sorted(mapping)]
                            self.assertTrue(
                                all(a < b for a, b in zip(positions, positions[1:])),
                                f"collapsed gap or reversed edge: {name}, axis={axis}",
                            )

    def test_paired_stems_keep_equal_nondecreasing_widths(self) -> None:
        # 0, 8 and H have two long native 20-unit vertical stems, at
        # x=10..30 and x=50..70. Independent rounding made 21px 3px/2px
        # and even shrank a stem from 4px at 27px to 3px at 28px.
        cmap = self.font.getBestCmap()
        for version in (35, 40):
            set_interpreter(version)
            face = freetype.Face(str(ROOT / "FixedsysEX.ttf"))
            for char in "08H":
                original, _, _ = self.font["glyf"][cmap[ord(char)]].getCoordinates(self.font["glyf"])
                previous = 0
                for size in range(17, 65):
                    face.set_pixel_sizes(0, size)
                    face.load_char(char, FLAGS)
                    mapping = {a[0]: b[0] for a, b in zip(original, face.glyph.outline.points)}
                    widths = [(mapping[30] - mapping[10]) // 64, (mapping[70] - mapping[50]) // 64]
                    expected = (size + 4) // 8
                    self.assertEqual(widths, [expected, expected], (version, char, size))
                    self.assertGreaterEqual(widths[0], previous, (version, char, size))
                    previous = widths[0]

    def test_currency_ink_does_not_decrease_when_size_increases(self) -> None:
        # This exact sample lost ink from 20px to 21px in the old build.
        for version in (35, 40):
            set_interpreter(version)
            face = freetype.Face(str(ROOT / "FixedsysEX.ttf"))
            previous = 0
            for size in range(17, 65):
                face.set_pixel_sizes(0, size)
                ink = 0
                for char in "$4.743708":
                    face.load_char(char, FLAGS | freetype.FT_LOAD_RENDER)
                    ink += sum(face.glyph.bitmap.buffer)
                self.assertGreaterEqual(ink, previous, (version, size))
                previous = ink

    def test_packed_profiles_execute_correctly_for_every_residue(self) -> None:
        cmap = self.font.getBestCmap()
        for version in (35, 40):
            set_interpreter(version)
            face = freetype.Face(str(ROOT / "FixedsysEX.ttf"))
            for char in SAMPLE:
                glyph = self.font["glyf"][cmap[ord(char)]]
                if not pixel_point_count(glyph, self.font["glyf"]):
                    continue
                original, _, _ = glyph.getCoordinates(self.font["glyf"])
                phases = [stem_phases(stem_spans(glyph, self.font["glyf"], axis)) for axis in (0, 1)]
                for size in range(17, 33):
                    face.set_pixel_sizes(0, size)
                    face.load_char(char, FLAGS)
                    expected = [
                        tuple(((point[axis] // 10) * size * 4 + phases[axis][size % 16]) // 64 * 64
                              for axis in (0, 1))
                        for point in original
                    ]
                    self.assertEqual(face.glyph.outline.points, expected, (version, char, size))

    def test_sample_is_crisp_at_multiple_sizes_in_ttf_and_woff2(self) -> None:
        for version in (35, 40):
            set_interpreter(version)
            for filename in ("FixedsysEX.ttf", "FixedsysEX-alt.ttf", "FixedsysEX.woff2", "FixedsysEX-alt.woff2"):
                path = ROOT / filename
                # Decode WOFF2 first: FreeType builds do not all support it.
                if path.suffix == ".woff2":
                    decoded = Path(self.temp.name) / (path.stem + "-decoded.ttf")
                    with TTFont(path) as font:
                        font.flavor = None
                        font.save(decoded)
                    path = decoded
                face = freetype.Face(str(path))
                for size in SIZES:
                    face.set_pixel_sizes(0, size)
                    for char in SAMPLE:
                        face.load_char(char, FLAGS | freetype.FT_LOAD_RENDER)
                        self.assertTrue(
                            all(value in (0, 255) for value in face.glyph.bitmap.buffer),
                            f"blurred pixels: interpreter={version}, file={filename}, size={size}, char={char}",
                        )

    def test_native_size_bitmaps_are_preserved(self) -> None:
        set_interpreter(40)
        original = freetype.Face(str(self.unhinted_path))
        hinted = freetype.Face(str(ROOT / "FixedsysEX.ttf"))
        for size in (16, 32):
            original.set_pixel_sizes(0, size)
            hinted.set_pixel_sizes(0, size)
            for name in self.font.getGlyphOrder():
                if not pixel_point_count(self.font["glyf"][name], self.font["glyf"]):
                    continue
                index = self.font.getGlyphID(name)
                original.load_glyph(index, FLAGS | freetype.FT_LOAD_RENDER)
                hinted.load_glyph(index, FLAGS | freetype.FT_LOAD_RENDER)
                self.assertEqual(hinted.glyph.bitmap.buffer, original.glyph.bitmap.buffer, (size, name))
                self.assertEqual(hinted.glyph.bitmap_left, original.glyph.bitmap_left, (size, name))
                self.assertEqual(hinted.glyph.bitmap_top, original.glyph.bitmap_top, (size, name))
                self.assertEqual(hinted.glyph.advance.x, original.glyph.advance.x, (size, name))

    def test_sizes_up_to_16_keep_the_original_outline(self) -> None:
        face = freetype.Face(str(ROOT / "FixedsysEX.ttf"))
        for size in (12, 14, 15, 16):
            face.set_pixel_sizes(0, size)
            for char in SAMPLE:
                face.load_char(char, FLAGS)
                hinted = face.glyph.outline.points
                face.load_char(char, FLAGS | freetype.FT_LOAD_NO_HINTING)
                self.assertEqual(hinted, face.glyph.outline.points, (size, char))


if __name__ == "__main__":
    unittest.main()
