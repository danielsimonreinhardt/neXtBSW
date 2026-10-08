"""Intel HEX reader for the image check (PLAT-80): record types 00, 01, 02, 04, 05 with checksum."""

from __future__ import annotations

from pathlib import Path


class HexError(ValueError):
    pass


def load(path: str | Path) -> dict[int, int]:
    """Bytes by address. A byte written twice with different values is an error (the file would be ambiguous)."""
    path = Path(path)
    image: dict[int, int] = {}
    base = 0
    for number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise HexError(f"{path}:{number}: record does not start with ':'")
        try:
            raw = bytes.fromhex(line[1:])
        except ValueError:
            raise HexError(f"{path}:{number}: not hexadecimal") from None
        if len(raw) < 5 or len(raw) != raw[0] + 5:
            raise HexError(f"{path}:{number}: wrong record length")
        if sum(raw) & 0xFF:
            raise HexError(f"{path}:{number}: checksum error")
        count, offset, kind, payload = raw[0], (raw[1] << 8) | raw[2], raw[3], raw[4:4 + raw[0]]
        if kind == 0:
            for i, b in enumerate(payload):
                address = base + offset + i
                if image.get(address, b) != b:
                    raise HexError(f"{path}:{number}: address 0x{address:08X} written twice with different values")
                image[address] = b
        elif kind == 1:
            break
        elif kind == 2 and count == 2:
            base = ((payload[0] << 8) | payload[1]) << 4
        elif kind == 4 and count == 2:
            base = ((payload[0] << 8) | payload[1]) << 16
        elif kind in (3, 5):
            pass                                    # start address: not part of the image
        else:
            raise HexError(f"{path}:{number}: unknown record type {kind:02X}")
    return image


def blocks(image: dict[int, int]) -> list[tuple[int, bytes]]:
    """Contiguous blocks (start, bytes) in address order."""
    result: list[tuple[int, bytearray]] = []
    for address in sorted(image):
        if result and result[-1][0] + len(result[-1][1]) == address:
            result[-1][1].append(image[address])
        else:
            result.append((address, bytearray([image[address]])))
    return [(start, bytes(data)) for start, data in result]
