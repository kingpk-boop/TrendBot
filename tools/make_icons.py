"""Draws frontend/public/icons/icon-192.png and icon-512.png (same design as icon.svg) with only the
standard library, so no image package is needed. Run: py -3 tools/make_icons.py"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "frontend" / "public" / "icons"
BG = (0x1A, 0x2A, 0x44)
WHITE = (255, 255, 255)
ORANGE = (0xEB, 0x68, 0x34)
LINE = [(96, 372), (196, 268), (268, 316), (416, 152)]
ARROW = [(443, 122), (436, 197), (370, 137)]


def seg_dist(px, py, a, b):
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def poly_dist(px, py, pts):
    return min(seg_dist(px, py, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def tri_cover(px, py, tri, aa):
    """Signed-distance coverage of a triangle (positive inside)."""
    d = []
    for i in range(3):
        (ax, ay), (bx, by) = tri[i], tri[(i + 1) % 3]
        nx, ny = by - ay, -(bx - ax)
        ln = math.hypot(nx, ny)
        d.append(((px - ax) * nx + (py - ay) * ny) / ln)
    # Orientation independent: pick the sign that puts the centroid inside.
    cx, cy = sum(p[0] for p in tri) / 3, sum(p[1] for p in tri) / 3
    (ax, ay), (bx, by) = tri[0], tri[1]
    sign = -1 if ((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) < 0 else 1
    inside = min(sign * x for x in d)
    return max(0.0, min(1.0, inside / aa + 0.5))


def rounded_rect_cover(px, py, size, r, aa):
    qx = abs(px - size / 2) - (size / 2 - r)
    qy = abs(py - size / 2) - (size / 2 - r)
    outside = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
    return max(0.0, min(1.0, 0.5 - outside / aa))


def blend(base, color, a):
    return tuple(b + (c - b) * a for b, c in zip(base, color))


def render(n: int) -> bytes:
    s = 512 / n          # icon units per pixel
    aa = s               # one pixel of anti-aliasing
    rows = []
    for y in range(n):
        row = bytearray([0])  # PNG filter type 0
        for x in range(n):
            px, py = (x + 0.5) * s, (y + 0.5) * s
            alpha = rounded_rect_cover(px, py, 512, 112, aa)
            col = BG
            if alpha > 0:
                shadow = [(a, b + 40) for a, b in LINE]
                col = blend(col, ORANGE, 0.9 * max(0.0, min(1.0, (15 - poly_dist(px, py, shadow)) / aa + 0.5)))
                col = blend(col, WHITE, max(0.0, min(1.0, (20 - poly_dist(px, py, LINE)) / aa + 0.5)))
                col = blend(col, WHITE, tri_cover(px, py, ARROW, aa))
            row += bytes([round(c) for c in col] + [round(alpha * 255)])
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    for size in (192, 512):
        (OUT / f"icon-{size}.png").write_bytes(render(size))
        print(f"wrote icon-{size}.png")
