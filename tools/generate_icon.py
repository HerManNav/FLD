"""Generate FLD's download-arrow and rabbit icon with the standard library."""

from pathlib import Path
import struct
import zlib


ROOT = Path(__file__).resolve().parents[1] / "assets"
TILE_RADIUS = 7
TILE_COLOR = "#14467d"
WHITE = "#ffffff"
TRAY_COLOR = "#4be191"
ARROW = [(17, 12), (27, 12), (27, 29), (34, 29), (22, 42), (10, 29), (17, 29)]
RABBIT = [
    (36, 23), (40, 20), (46, 20), (49, 17), (48, 7), (51, 6), (54, 16),
    (56, 6), (59, 7), (58, 17), (61, 19), (61, 22), (56, 24), (53, 25),
    (60, 29), (59, 32), (51, 28), (48, 25), (46, 30), (51, 35), (49, 38),
    (41, 32), (38, 27),
]
TAIL = [(35, 22), (38, 22), (40, 25), (38, 28), (35, 27), (34, 24)]
TRAY = [(13, 43), (19, 43), (19, 49), (45, 49), (45, 43), (51, 43), (51, 55), (13, 55)]
SHAPES = (
    (ARROW, WHITE),
    (RABBIT, WHITE),
    (TAIL, WHITE),
    (TRAY, TRAY_COLOR),
)
SIZES = (16, 24, 32, 48, 64)
SUPERSAMPLING = 4


def inside(x, y, points):
    result = False
    previous = points[-1]
    for current in points:
        ax, ay = previous
        bx, by = current
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            result = not result
        previous = current
    return result


def pixels(size):
    rows = []
    colors = {
        TILE_COLOR: (20, 70, 125, 255),
        WHITE: (255, 255, 255, 255),
        TRAY_COLOR: (75, 225, 145, 255),
    }
    for y in range(size):
        row = bytearray()
        for x in range(size):
            channels = [0, 0, 0, 0]
            for sy in range(SUPERSAMPLING):
                for sx in range(SUPERSAMPLING):
                    px = (x + (sx + .5) / SUPERSAMPLING) * 64 / size
                    py = (y + (sy + .5) / SUPERSAMPLING) * 64 / size
                    dx, dy = max(TILE_RADIUS - px, 0, px - (64 - TILE_RADIUS)), max(
                        TILE_RADIUS - py, 0, py - (64 - TILE_RADIUS)
                    )
                    color = colors[TILE_COLOR] if dx * dx + dy * dy <= TILE_RADIUS**2 else (0, 0, 0, 0)
                    for points, fill in SHAPES:
                        if inside(px, py, points):
                            color = colors[fill]
                    for index, value in enumerate(color):
                        channels[index] += value
            row.extend(round(value / (SUPERSAMPLING**2)) for value in channels)
        rows.append(bytes(row))
    return rows


def svg_document():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="{TILE_RADIUS}" fill="{TILE_COLOR}"/>'
    )
    for points, color in SHAPES:
        coordinates = " ".join(f"{x},{y}" for x, y in points)
        svg += f'<polygon points="{coordinates}" fill="{color}"/>'
    return svg + "</svg>\n"


def png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def write_ico():
    chunks, entries = [], []
    offset = 6 + len(SIZES) * 16
    for size in SIZES:
        rows = pixels(size)
        image = b"".join(
            bytes(channel for x in range(size) for channel in (row[4*x+2], row[4*x+1], row[4*x], row[4*x+3]))
            for row in reversed(rows)
        )
        mask = b"\0" * (((size + 31) // 32) * 4 * size)
        bitmap = struct.pack("<IIIHHIIIIII", 40, size, size * 2, 1, 32, 0, len(image), 0, 0, 0, 0) + image + mask
        entries.append(struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(bitmap), offset))
        chunks.append(bitmap)
        offset += len(bitmap)
    ico = struct.pack("<HHH", 0, 1, len(SIZES)) + b"".join(entries) + b"".join(chunks)
    (ROOT / "fld.ico").write_bytes(ico)


def write_png():
    png = b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 6, 0, 0, 0))
    png += png_chunk(b"IDAT", zlib.compress(b"".join(b"\0" + row for row in pixels(64)))) + png_chunk(b"IEND", b"")
    (ROOT / "fld.png").write_bytes(png)


def main():
    ROOT.mkdir(exist_ok=True)
    (ROOT / "fld.svg").write_text(svg_document(), encoding="utf-8")
    write_ico()
    write_png()


if __name__ == "__main__":
    main()
