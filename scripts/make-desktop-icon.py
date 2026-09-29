"""Render the EF lettermark to a multi-resolution Windows icon (standard library)."""
import struct
import zlib
from pathlib import Path


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(size):
    letters = ["11111011111", "10000010000", "10000010000", "11110011110", "10000010000", "10000010000", "11111010000"]
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            u, v = (x + .5) / size, (y + .5) / size
            dx, dy = max(.19 - u, 0, u - .81), max(.19 - v, 0, v - .81)
            alpha = 255 if dx * dx + dy * dy <= .19 ** 2 else 0
            gx, gy = int((u - .19) / .62 * 11), int((v - .29) / .42 * 7)
            on = .19 <= u < .81 and .29 <= v < .71 and letters[gy][gx] == "1"
            raw.extend((*((219, 238, 216) if on else (26, 67, 60)), alpha))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def main():
    sizes = (16, 32, 48, 64, 128, 256)
    images = [png(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = []
    for size, data in zip(sizes, images):
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    target = Path(__file__).resolve().parents[1] / "evidenceforge" / "static" / "desktop.ico"
    target.write_bytes(header + b"".join(entries) + b"".join(images))


if __name__ == "__main__":
    main()
