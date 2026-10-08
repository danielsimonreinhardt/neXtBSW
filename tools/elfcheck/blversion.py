"""Bootloader image changed -> bootloader version changed (BL-32).

  python tools/elfcheck blversion [--config bootloader/include/bl_config.h] [--table bootloader/test/image_version.sha256]
                                  [--compiler <arm-none-eabi-gcc>] [--add] <bootloader.hex>

A bootloader is flashed rarely and identified in the field only by its number (DID 0xF180/0xF189, canflash,
updater). Two different images under one number cannot be told apart any more - on 2026-09-29 the Eval Board and
dev both reported 0.3.4 and differed in 81 bytes. The table holds the SHA-256 of the image of the reference preset
per BL_VERSION_NUMBER; the check compares the image of the current build with the entry of the current number:

- entry present, hash equal: OK;
- entry present, hash different: the image changed without a new number -> red, "raise BL_VERSION_NUMBER";
- no entry for the number: red with the line to add (`--add` appends it; an existing entry is never rewritten).

What is hashed: the bytes that go into the flash, from the Intel hex file, gaps filled with 0xFF from the first to
the last written address (the same bytes as `objcopy -O binary --gap-fill 0xff`). No debug information, no path,
no time stamp - the hash does not depend on the build or source directory. It does depend on the compiler: another
GCC generates other code. Each entry names the compiler version it was taken with (arm-none-eabi-gcc -dumpversion);
a build with another version is not comparable (exit code 3, tools/test reports it as skipped). The member order
inside the toolchain's archives no longer matters since the bootloader linker script sorts library members by
section name (BL-32).

Exit code 0 OK, 1 image changed or entry missing, 2 unusable input, 3 other compiler (not comparable).
Standard library only, no Qt.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ihex  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "bootloader" / "include" / "bl_config.h"
DEFAULT_TABLE = ROOT / "bootloader" / "test" / "image_version.sha256"

EXIT_OK, EXIT_CHANGED, EXIT_INPUT, EXIT_OTHER_COMPILER = 0, 1, 2, 3

_VERSION_RE = re.compile(r'^\s*#\s*define\s+BL_VERSION_NUMBER\s+"(\d+\.\d+\.\d+)"', re.MULTILINE)
_LINE_RE = re.compile(r"^(\d+\.\d+\.\d+)\s+([0-9a-f]{64})\s+(\S+)\s*(?:#.*)?$")


@dataclass(frozen=True)
class Entry:
    version: str
    sha256: str
    compiler: str
    line: int


@dataclass(frozen=True)
class Result:
    code: int
    message: str


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def read_version(config_h: str | Path) -> str:
    """BL_VERSION_NUMBER of bl_config.h."""
    match = _VERSION_RE.search(Path(config_h).read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"{config_h}: no #define BL_VERSION_NUMBER \"x.y.z\"")
    return match.group(1)


def parse_table(text: str, source: str = "table") -> dict[str, Entry]:
    """Entries by version. Comment lines (#) and empty lines are skipped; a version twice or out of ascending order
    is an error (the table is a history, appended at the end)."""
    entries: dict[str, Entry] = {}
    last: str | None = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            raise ValueError(f"{source}:{number}: expected '<x.y.z> <sha256, 64 hex digits> <gcc version>'")
        version, digest, compiler = match.groups()
        if version in entries:
            raise ValueError(f"{source}:{number}: version {version} twice (first in line {entries[version].line})")
        if last is not None and version_key(version) <= version_key(last):
            raise ValueError(f"{source}:{number}: version {version} not above {last} - append new versions at the end")
        entries[version] = Entry(version, digest, compiler, number)
        last = version
    return entries


def image_bytes(image: dict[int, int]) -> bytes:
    """Flash content from the first to the last written address, gaps 0xFF."""
    if not image:
        raise ValueError("empty image")
    low, high = min(image), max(image)
    data = bytearray(b"\xff" * (high - low + 1))
    for address, value in image.items():
        data[address - low] = value
    return bytes(data)


def image_sha256(hex_path: str | Path) -> tuple[str, bytes]:
    data = image_bytes(ihex.load(hex_path))
    return hashlib.sha256(data).hexdigest(), data


def compiler_version(compiler: str | Path) -> str:
    out = subprocess.run([str(compiler), "-dumpversion"], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def evaluate(version: str, digest: str, compiler: str, entries: dict[str, Entry], image: bytes | None = None,
             table_name: str = "image_version.sha256") -> Result:
    """The rule itself, without files: current number, hash and compiler version against the table."""
    if image is not None and version.encode("ascii") not in image:
        return Result(EXIT_INPUT, f"the image does not contain the version text '{version}' - build and bl_config.h "
                                  "are not the same state (build the bootloader again)")
    entry = entries.get(version)
    if entry is None:
        newer = [v for v in entries if version_key(v) > version_key(version)]
        if newer:
            return Result(EXIT_CHANGED, f"BL_VERSION_NUMBER {version} is below the newest entry {max(newer, key=version_key)} "
                                        f"in {table_name} - the number must only go up")
        return Result(EXIT_CHANGED, f"no entry for bootloader {version} in {table_name}: append the line "
                                    f"'{version} {digest} {compiler}' (tools/elfcheck blversion --add)")
    if entry.compiler != compiler:
        return Result(EXIT_OTHER_COMPILER, f"entry {version} (line {entry.line}) was taken with arm-none-eabi-gcc "
                                           f"{entry.compiler}, this build uses {compiler} - not comparable")
    if entry.sha256 != digest:
        return Result(EXIT_CHANGED, f"bootloader image changed, BL_VERSION_NUMBER is still {version} "
                                    f"(entry line {entry.line}: {entry.sha256[:16]}..., build {digest[:16]}...): "
                                    "raise BL_VERSION_NUMBER in bootloader/include/bl_config.h (BL_VERSION_NUMBER "
                                    "hochziehen) and append the new hash - every changed image needs its own number")
    same = [e.version for e in entries.values() if e.sha256 == digest and e.version != version]
    if same:
        return Result(EXIT_CHANGED, f"hash of {version} equals the entry of {same[0]} - two numbers for one image")
    return Result(EXIT_OK, f"bootloader {version}: image unchanged ({digest[:16]}..., gcc {compiler})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="elfcheck blversion", description="Bootloader image changed -> "
                                     "BL_VERSION_NUMBER changed (BL-32)")
    parser.add_argument("hex", type=Path, help="bootloader.hex of the reference preset (evalboard-s32k144-demo)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="bl_config.h with BL_VERSION_NUMBER")
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE, help="hash per version")
    parser.add_argument("--compiler", default="arm-none-eabi-gcc", help="compiler of the build (-dumpversion)")
    parser.add_argument("--add", action="store_true", help="append the entry of a version that has none yet")
    args = parser.parse_args(argv)
    try:
        version = read_version(args.config)
        digest, data = image_sha256(args.hex)
        compiler = compiler_version(args.compiler)
        text = args.table.read_text(encoding="utf-8") if args.table.is_file() else ""
        entries = parse_table(text, str(args.table))
    except (OSError, ValueError, subprocess.CalledProcessError, ihex.HexError) as e:
        print(f"elfcheck blversion: {e}", file=sys.stderr)
        return EXIT_INPUT
    result = evaluate(version, digest, compiler, entries, data, args.table.name)
    appendable = version not in entries and all(version_key(v) < version_key(version) for v in entries)
    if args.add and appendable and result.code == EXIT_CHANGED:
        with args.table.open("a", encoding="utf-8", newline="\n") as f:
            if text and not text.endswith("\n"):
                f.write("\n")
            f.write(f"{version} {digest} {compiler}\n")
        print(f"elfcheck blversion: {args.table.name}: added {version} {digest} {compiler}")
        return EXIT_OK
    print(f"elfcheck blversion: {result.message}", file=sys.stderr if result.code else sys.stdout)
    return result.code


if __name__ == "__main__":
    sys.exit(main())
