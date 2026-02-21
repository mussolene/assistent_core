"""Generate a minimal 32x32 favicon.png (no Pillow). Run from repo root: python -m assistant.dashboard.make_favicon"""
import struct
import zlib
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
OUT = _THIS_DIR / "static" / "favicon.png"


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    chunk = chunk_type + data
    crc = zlib.crc32(chunk) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk + struct.pack(">I", crc)


def main():
    out = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0)
    out += png_chunk(b"IHDR", ihdr)
    raw = b""
    dark = bytes([12, 12, 15])
    accent = bytes([34, 197, 94])
    for y in range(32):
        raw += b"\x00"
        for x in range(32):
            if 10 <= x < 22 and 10 <= y < 22:
                raw += accent
            else:
                raw += dark
    out += png_chunk(b"IDAT", zlib.compress(raw, 9))
    out += png_chunk(b"IEND", b"")
    OUT.write_bytes(out)
    print(f"Wrote {OUT} ({len(out)} bytes)")


if __name__ == "__main__":
    main()
