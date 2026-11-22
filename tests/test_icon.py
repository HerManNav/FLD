import struct
import unittest
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

from tools import generate_icon


ROOT = Path(__file__).resolve().parents[1] / "assets"


class IconAssetTests(unittest.TestCase):
    def test_svg_uses_shared_geometry_and_colors(self):
        root = ET.fromstring((ROOT / "fld.svg").read_text(encoding="utf-8"))
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        tile = root.find("svg:rect", namespace)
        self.assertEqual(tile.attrib["rx"], str(generate_icon.TILE_RADIUS))
        self.assertEqual(tile.attrib["fill"], generate_icon.TILE_COLOR)

        polygons = root.findall("svg:polygon", namespace)
        self.assertEqual(len(polygons), len(generate_icon.SHAPES))
        for polygon, (points, color) in zip(polygons, generate_icon.SHAPES):
            expected = " ".join(f"{x},{y}" for x, y in points)
            self.assertEqual(polygon.attrib, {"points": expected, "fill": color})

    def test_png_dimensions_and_ico_frames(self):
        png = (ROOT / "fld.png").read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", png[16:24]), (64, 64))
        self.assertEqual(zlib.crc32(png[12:29]), struct.unpack(">I", png[29:33])[0])

        ico = (ROOT / "fld.ico").read_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", ico)
        self.assertEqual((reserved, kind, count), (0, 1, len(generate_icon.SIZES)))
        frames = []
        for index in range(count):
            width, height, _, _, planes, depth, length, offset = struct.unpack_from(
                "<BBBBHHII", ico, 6 + index * 16
            )
            frames.append((width or 256, height or 256))
            header_size, bitmap_width, bitmap_height, bitmap_planes, bitmap_depth = struct.unpack_from(
                "<IiiHH", ico, offset
            )
            self.assertEqual((header_size, bitmap_width, bitmap_height // 2), (40, width, height))
            self.assertEqual((bitmap_planes, bitmap_depth, planes, depth), (1, 32, 1, 32))
            self.assertEqual(length, len(ico[offset:offset + length]))
        self.assertEqual(frames, [(size, size) for size in generate_icon.SIZES])


if __name__ == "__main__":
    unittest.main()
