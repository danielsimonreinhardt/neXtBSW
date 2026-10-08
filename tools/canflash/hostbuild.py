"""Host builds (Zig/Clang) of bootloader code with the memory layout generated from a project (ARCH-08).

bootloader/include/bl_config.h includes the generated bootloader/bl_memory.h and bootloader/bl_diag.h, so every host
build needs a generated directory on its include path: generate_layout() writes it with tools/bswgen/blgen.py from any
project (eval board or a test profile), exactly like the target build.

PLAT-16 adds two things the generator does not write yet (diff proposal in doc/changelog.d/PLAT-16.md):
- the sector table of the code flash (BL_SECTOR_GROUPS), appended here from the derivative database, and
- synthetic layouts from bootloader/test/layouts/*.yaml for derivatives and partitionings without a project profile
  (S32K116 with 2-KB sectors, STM32F446 with 16/64/128-KB sectors, record sector below the application area).
Both go through the same emitter, sector_table_macros(), so the host tests and the proposed generator output cannot
drift apart. The MCU family (BL_FAMILY, BL-08) and the plain text hardware identity (BL_HARDWARE_ID) are written by
blgen itself since BL-10; synthetic layouts write the same text (family_macro(), hardware_macro()). The family
selects the family hooks bootloader/platforms/<family> a host build compiles the core with, and the image rule
of tools/canflash. A synthetic layout carries its own diagnostic addresses (`diagnostics:`) and gets its bl_diag.h from
the generator's own emitter (blgen.diag_header), for the same reason.

COM-09: every host library is built against the **checksum of its sources** (checksum(), build_if_changed()), not
against modification times. A stale DLL that was newer than its sources made a test red and looked like a time
dependent failure; a checksum next to the library (<library>.sources.sha256) cannot be fooled that way and skips
the build of an unchanged tree.
"""

import hashlib
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_LAYOUTS = ROOT / "bootloader" / "test" / "layouts"
# diagnostic addresses of a synthetic layout without a `diagnostics:` section (bl_diag.h, COM-11)
DEFAULT_DIAG = {"request_id": 0x7E0, "functional_id": 0x7DF, "response_id": 0x7E8, "padding": 0xCC,
                "block_size": 0, "stmin_ms": 0, "n_cr_ms": 1000, "n_bs_ms": 1000, "tester_address": 0xF1,
                "ecu_address": 0x2A, "version_prefix": "nextbsw-bl", "scan_start": None}
# BL-03: master key of synthetic layouts (tools/nxconfig/model.py INSECURE_TEST_KEY_NAME, tools/canflash/keys)
TEST_KEY_NAME = "nextbsw-insecure-test"
# Host part of the security access (BL-03): simulated chip UID and random generator, SHA-256/HMAC entry points
HOST_SECURITY_SOURCE = Path(__file__).resolve().parent / "host" / "bl_host_security.c"


# ------------------------------------------------------------------ build against the source checksum (COM-09)


def checksum(sources: Iterable[Path], extra: Iterable[str] = ()) -> str:
    """SHA-256 over the contents of `sources` and the strings `extra` (compiler, flags, include paths).

    Why not the modification time: a host library that is older than its sources is rebuilt by a mtime check,
    but a library that is *newer* than a source which changed back and forth, was restored from a build cache
    or came from another branch is not. Such a stale app_uds_host.dll made the test app-uds-host red and
    looked like a time dependent failure (COM-11 finding, 2026-09-16); the checksum cannot be fooled that way.
    A missing file is part of the checksum as its name only, so creating it changes the result."""
    digest = hashlib.sha256()
    for item in extra:
        digest.update(b"\x00arg\x00")
        digest.update(str(item).encode("utf-8"))
    for path in sorted({Path(p) for p in sources}, key=lambda p: p.as_posix()):
        digest.update(b"\x00file\x00")
        digest.update(path.name.encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def stamp_file(out: Path) -> Path:
    """File next to the built library that holds the checksum of the sources it was built from."""
    return Path(out).with_suffix(Path(out).suffix + ".sources.sha256")


def is_current(out: Path, digest: str) -> bool:
    """True if `out` exists and was built from sources with this checksum."""
    stamp = stamp_file(out)
    if not (Path(out).is_file() and stamp.is_file()):
        return False
    return stamp.read_text(encoding="utf-8").strip() == digest


def mark_current(out: Path, digest: str) -> None:
    stamp_file(out).write_text(digest + "\n", encoding="utf-8", newline="\n")


def build_if_changed(out: Path, digest: str, build) -> bool:
    """Calls build() unless `out` already belongs to this checksum; returns True if it was built.

    The stamp is written only after a successful build, so an aborted or failed build is repeated."""
    if is_current(out, digest):
        return False
    stamp_file(out).unlink(missing_ok=True)  # a half finished library must not look current
    build()
    mark_current(out, digest)
    return True


def _blgen():
    sys.path.insert(0, str(ROOT / "tools" / "bswgen"))
    import blgen  # noqa: E402
    return blgen


def generate_layout(project: Path, out_dir: Path, mcudb: Path | None = None) -> Path:
    """Writes <out_dir>/bootloader/bl_memory.h, bl_diag.h (and the linker values) of `project`; returns out_dir."""
    blgen = _blgen()
    kwargs = {"mcudb": mcudb} if mcudb is not None else {}
    config = blgen.generate(Path(project), Path(out_dir), board=False, **kwargs)
    append_sector_table(Path(out_dir) / blgen.MEMORY_HEADER, config)
    check_family(Path(out_dir), config.derivative.derivative.family)
    return Path(out_dir)


# ------------------------------------------------------------------------------------------- MCU family (BL-08)


def family_macro(family: str) -> str:
    """The BL_FAMILY block of a synthetic bl_memory.h (write_test_layout()), same text as the generator writes
    (tools/bswgen/templates/bl_memory.h.j2, BL-10). The C code does not use it, the build selects
    bootloader/platforms/<family> by NX_FAMILY - host builds and tools/canflash read it here instead."""
    return "\n".join([
        "",
        "/* MCU family of the derivative (BL-08): family hooks bootloader/platforms/<family> (application start, updater",
        " * image start and stub), image rule of tools/canflash */",
        f'#define BL_FAMILY "{family}"',
        "",
    ])


def hardware_macro(board: str, derivative: str) -> str:
    """The BL_HARDWARE_ID block of a synthetic bl_memory.h, same text as the generator writes (BL-10)."""
    return "\n".join([
        "",
        "/* Board and derivative in plain text (BL-10): answer of DID 0xF191 vehicleManufacturerECUHardwareNumber. The",
        " * identity above is the CRC-32 of this string. */",
        f'#define BL_HARDWARE_ID "{board}/{derivative}"',
    ])


def check_family(layout_dir: Path, family: str) -> None:
    """The generated bl_memory.h names the derivative's family (blgen writes BL_FAMILY since BL-10)."""
    found = layout_family(layout_dir)
    if found != family:
        raise ValueError(f"{layout_dir}: BL_FAMILY {found!r}, the derivative's family is {family!r}")


def layout_family(layout_dir: Path) -> str:
    """BL_FAMILY of <layout_dir>/bootloader/bl_memory.h (generate_layout(), write_test_layout())."""
    import re

    header = Path(layout_dir) / "bootloader" / "bl_memory.h"
    found = re.search(r'#define\s+BL_FAMILY\s+"([\w-]+)"', header.read_text(encoding="utf-8"))
    if found is None:
        raise ValueError(f"{header}: no BL_FAMILY - written by tools/bswgen/blgen.py (BL-10) or write_test_layout()")
    return found.group(1)


def family_sources(source: Path, family: str) -> tuple[Path, list[Path]]:
    """Include directory of the family hooks and the files in it a host build depends on (checksum).

    A family without bootloader/platforms/<family> is a synthetic family of a test profile (testfam of
    tools/nxconfig/test/mcudb) - its target build has no bootloader anyway (bootloader/CMakeLists.txt stops without a
    HAL). Its host build takes the host test family bootloader/test/host/family: Cortex-M application start, like
    the images the host tests write."""
    platform = Path(source) / "bootloader" / "platforms" / family
    if not platform.is_dir():
        platform = Path(source) / "bootloader" / "test" / "host" / "family"
    # rules shared by several families (BL-11), included as "arch/<file>" through -I bootloader/platforms/common
    arch = Path(source) / "bootloader" / "platforms" / "common" / "arch"
    files = [*sorted(platform.glob("*.inc")), *sorted(platform.glob("upd_family*.h")), *sorted(arch.glob("*"))]
    return platform, files


# --------------------------------------------------------------------------------------- sector table (PLAT-16)


def sector_groups(table: list[tuple[int, int]]) -> list[tuple[int, int, int]]:
    """(start, size, count) groups of consecutive equally sized sectors from a (start, size) sector table."""
    groups: list[tuple[int, int, int]] = []
    for start, size in table:
        if groups and groups[-1][1] == size and (groups[-1][0] + groups[-1][1] * groups[-1][2]) == start:
            groups[-1] = (groups[-1][0], size, groups[-1][2] + 1)
        else:
            groups.append((start, size, 1))
    return groups


def sector_table_macros(groups: list[tuple[int, int, int]], app_start: int) -> str:
    """The BL_SECTOR_* block of bl_memory.h: the sector geometry of the code flash region as data.

    This is exactly what tools/bswgen/blgen.py should render (PLAT-16 diff proposal). BL_SECTOR_GROUPS is a comma
    separated initialiser list, used as `{BL_SECTOR_GROUPS}` in bootloader/src/core/bl_sectors.h. BL_SECTOR_SIZE
    keeps its meaning "the one sector size" and is 0 when the sectors differ."""
    if not groups:
        raise ValueError("no sector table: the code flash region of the derivative has neither sectors nor sector_size")
    boot_max = max((size for start, size, count in groups
                    for sector in range(count)
                    if (start + sector * size) < app_start), default=0)
    lines = [
        "",
        "/* Sector table of the code flash (derivative memory[].sectors / sector_size, PLAT-16): groups of",
        " * consecutive equally sized sectors in address order. The bootloader core and the updater iterate over the",
        " * table instead of masking with a sector size, because a region may have sectors of different sizes",
        " * (STM32F4: 16/64/128 KB). BL_SECTOR_SIZE above is 0 in that case. */",
        f"#define BL_SECTOR_GROUP_COUNT {len(groups)}U",
        "#define BL_SECTOR_GROUPS \\",
    ]
    for index, (start, size, count) in enumerate(groups):
        last = index == len(groups) - 1
        lines.append(f"    {{{start:#010x}UL, {size:#010x}UL, {count}U}}" + ("" if last else ", \\"))
    lines += [
        f"#define BL_SECTOR_COUNT {sum(count for _, _, count in groups)}U",
        f"#define BL_SECTOR_MAX_SIZE {max(size for _, size, _ in groups):#010x}UL",
        "/* Largest sector of the bootloader area: size of the updater's sector buffers */",
        f"#define BL_BOOT_SECTOR_MAX_SIZE {boot_max:#010x}UL",
        "",
    ]
    return "\n".join(lines)


def flash_sector_groups(config) -> list[tuple[int, int, int]]:
    """Sector groups of the code flash region a project uses (from the derivative database)."""
    name = config.memory.flash_region
    region = next(r for r in config.derivative.memory if r.name == name)
    return sector_groups(region.sector_table())


def append_sector_table(header: Path, config) -> list[tuple[int, int, int]]:
    """Adds the sector table of the project's code flash to a generated bl_memory.h (blgen does not write it yet)."""
    groups = flash_sector_groups(config)
    text = header.read_text(encoding="utf-8")
    if "BL_SECTOR_GROUPS" not in text:
        header.write_text(text + sector_table_macros(groups, config.memory.app_start),
                          encoding="utf-8", newline="\n")
    return groups


def load_test_layout(name: str) -> dict:
    """Synthetic layout from bootloader/test/layouts/<name>.yaml (derivatives without a project profile)."""
    from ruamel.yaml import YAML

    path = name if Path(name).is_file() else TEST_LAYOUTS / f"{name}.yaml"
    data = YAML(typ="safe").load(Path(path).read_text(encoding="utf-8"))
    data["file"] = str(path)
    return data


def test_layout_diag(layout: dict) -> dict:
    """Diagnostic addresses of a synthetic layout (`diagnostics:`), defaults filled in."""
    given = layout.get("diagnostics") or {}
    unknown = set(given) - set(DEFAULT_DIAG)
    if unknown:
        raise ValueError(f"{layout.get('file')}: unknown diagnostics keys {', '.join(sorted(unknown))}")
    values = dict(DEFAULT_DIAG, **given)
    if values["scan_start"] is None:
        values["scan_start"] = layout["flash"]["start"]
    return values


def test_layout_key_sector(layout: dict) -> tuple[int, int, int, int]:
    """(key address, key sector size, application start, application end) of a synthetic layout (BL-03).

    The key sector of the security access lies next to the validity record, on the side of the application: below
    the record when the record is the last sector (tools/nxconfig/memory.py does the same), directly above it when
    the record lies below the application (STM32F4 partitioning). The application area of the layout file loses
    that sector. `key_address` in the layout file names the sector instead."""
    table = _table(layout)
    sizes = dict(table)
    app_start, app_end, record = layout["app_start"], layout["app_end"], layout["record_address"]
    if "key_address" in layout:
        key = layout["key_address"]
    elif record >= app_end:
        key = max(start for start, _ in table if start < record)
    else:
        key = app_start
    if key not in sizes:
        raise ValueError(f"{layout.get('file')}: key sector 0x{key:X} does not start a sector")
    size = sizes[key]
    if key == app_start:
        app_start = key + size
    elif key + size == app_end:
        app_end = key
    elif not (key >= app_end or key + size <= app_start):
        raise ValueError(f"{layout.get('file')}: key sector 0x{key:X} inside the application area")
    return key, size, app_start, app_end


def test_layout_values(layout: dict) -> dict[str, int]:
    """The ten LAYOUT_KEYS plus BL_TARGET_ID and the key sector (BL-03) of a synthetic layout (same names as the
    generated header). The application area is the one of the layout file minus the key sector."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import canflash  # noqa: E402

    flash, ram = layout["flash"], layout["ram"]
    sizes = {size for _, size in _table(layout)}
    key, key_size, app_start, app_end = test_layout_key_sector(layout)
    return {
        "BL_FLASH_START": flash["start"],
        "BL_FLASH_SIZE": flash["size"],
        "BL_SECTOR_SIZE": sizes.pop() if len(sizes) == 1 else 0,
        "BL_PHRASE_SIZE": flash["program_size"],
        "BL_APP_START": app_start,
        "BL_APP_END": app_end,
        "BL_APP_RECORD_ADDR": layout["record_address"],
        "BL_RAM_START": ram["start"],
        "BL_RAM_END": ram["end"],
        "BL_BOOT_REQUEST_ADDR": layout["boot_request"],
        "BL_TARGET_ID": canflash.target_id(layout["target"]["board"], layout["target"]["derivative"]),
        "BL_KEY_ADDR": key,
        "BL_KEY_AREA_SIZE": key_size,
    }


def test_layout_uid(layout: dict) -> tuple[int, int, str]:
    """(address, size, source) of the chip UID of a synthetic layout: `uid:` of the file, else the derivative's
    entry in mcudb (mcudb/<family>/<derivative>.yaml uid)."""
    if "uid" in layout:
        return layout["uid"]["address"], layout["uid"]["size"], Path(layout["file"]).name
    from ruamel.yaml import YAML

    derivative = layout["target"]["derivative"]
    path = ROOT / "mcudb" / layout["family"] / f"{derivative}.yaml"
    data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    uid = (data or {}).get("uid")
    if uid is None:
        raise ValueError(f"{layout.get('file')}: no uid and no mcudb entry with uid for {derivative}")
    return uid["address"], uid["size"], f"mcudb {derivative} uid"


def _table(layout: dict) -> list[tuple[int, int]]:
    flash = layout["flash"]
    groups = [(g["size"], g["count"]) for g in flash["sectors"]] if "sectors" in flash \
        else [(flash["sector_size"], flash["size"] // flash["sector_size"])]
    table, address = [], flash["start"]
    for size, count in groups:
        for _ in range(count):
            table.append((address, size))
            address += size
    return table


def write_test_layout(name: str, out_dir: Path) -> Path:
    """Writes <out_dir>/bootloader/bl_memory.h and bl_memory.ld of a synthetic layout; returns out_dir.

    The macro and symbol names and their meaning are those of the generated files, so neither the C code nor a
    linker script can tell the difference (bl_memory.ld is what the target link test of a family HAL needs)."""
    layout = load_test_layout(name)
    values = test_layout_values(layout)
    groups = sector_groups(_table(layout))
    header = Path(out_dir) / "bootloader" / "bl_memory.h"
    header.parent.mkdir(parents=True, exist_ok=True)
    text = [
        f"/* Written by tools/canflash/hostbuild.py from {Path(layout['file']).name} - do not edit.",
        " * Synthetic memory layout for host tests (PLAT-16); same macros as the generated bootloader/bl_memory.h.",
        f" * {layout['source']} */",
        "",
        "#pragma once",
        "",
        f"/* Code flash of {layout['target']['derivative']}: {values['BL_FLASH_START']:#010x}.."
        f"{values['BL_FLASH_START'] + values['BL_FLASH_SIZE'] - 1:#010x}, phrase {values['BL_PHRASE_SIZE']} bytes,"
        + (f" sectors {values['BL_SECTOR_SIZE']:#x} */" if values["BL_SECTOR_SIZE"] else " unequal sectors */"),
    ]
    for key in ("BL_FLASH_START", "BL_FLASH_SIZE", "BL_SECTOR_SIZE", "BL_PHRASE_SIZE", "BL_APP_START", "BL_APP_END",
                "BL_APP_RECORD_ADDR", "BL_RAM_START", "BL_RAM_END", "BL_BOOT_REQUEST_ADDR"):
        text.append(f"#define {key:<20} {values[key]:#010x}UL")
    text += [
        "",
        "/* Device key of the security access (BL-03): key sector next to the validity record */",
        f"#define BL_KEY_ADDR        {values['BL_KEY_ADDR']:#010x}UL",
        f"#define BL_KEY_AREA_SIZE   {values['BL_KEY_AREA_SIZE']:#010x}UL",
        "",
        f'/* Board/derivative identity (BL-02): CRC-32 over "{layout["target"]["board"]}/'
        f'{layout["target"]["derivative"]}" */',
        f"#define BL_TARGET_ID {values['BL_TARGET_ID']:#010x}UL",
        hardware_macro(layout["target"]["board"], layout["target"]["derivative"]),
        sector_table_macros(groups, values["BL_APP_START"]),
        family_macro(layout["family"]),
    ]
    header.write_text("\n".join(text), encoding="utf-8", newline="\n")

    # bl_diag.h through the generator's own emitter, so a synthetic layout cannot drift apart from a project
    diag = test_layout_diag(layout)
    blgen = _blgen()
    diag_values = {
        "BL_CAN_ID_REQUEST_PHYS": diag["request_id"], "BL_CAN_ID_REQUEST_FUNC": diag["functional_id"],
        "BL_CAN_ID_RESPONSE": diag["response_id"], "BL_ISOTP_PADDING": diag["padding"],
        "BL_ISOTP_BLOCK_SIZE": diag["block_size"], "BL_ISOTP_STMIN_MS": diag["stmin_ms"],
        "BL_ISOTP_N_CR_MS": diag["n_cr_ms"], "BL_ISOTP_N_BS_MS": diag["n_bs_ms"],
        "NX_DIAG_TESTER_ADDRESS": diag["tester_address"], "NX_DIAG_ECU_ADDRESS": diag["ecu_address"],
        "NX_DIAG_BL_SCAN_START": diag["scan_start"], "NX_DIAG_BL_SCAN_END": values["BL_APP_START"],
    }
    (header.parent / "bl_diag.h").write_text(
        blgen.diag_header(diag_values, diag["version_prefix"],
                          f"{Path(layout['file']).name} (synthetic layout, tools/canflash/hostbuild.py)"),
        encoding="utf-8", newline="\n")

    # bl_security.h (BL-03) through the generator's emitter as well: the insecure test key, allowed for test data
    uid_address, uid_size, uid_source = test_layout_uid(layout)
    (header.parent / "bl_security.h").write_text(
        blgen.security_header({"BL_SECURITY_METHOD": 1, "BL_SECURITY_TEST_KEY_ALLOWED": 1, "BL_UID_ADDR": uid_address,
                               "BL_UID_LEN": uid_size}, "hmac-sha256", TEST_KEY_NAME,
                              f"{Path(layout['file']).name} (synthetic layout, tools/canflash/hostbuild.py)",
                              uid_source),
        encoding="utf-8", newline="\n")

    if "bootloader_ram" in layout:
        ram = layout["bootloader_ram"]
        (header.parent / "bl_memory.ld").write_text("\n".join([
            f"/* Written by tools/canflash/hostbuild.py from {Path(layout['file']).name} - do not edit. */",
            f"BL_FLASH_ORIGIN = {values['BL_FLASH_START']:#010x};",
            f"BL_AREA_SIZE    = {layout['bootloader_size']:#010x};",
            f"BL_APP_ORIGIN   = {values['BL_APP_START']:#010x};",
            f"BL_APP_LIMIT    = {values['BL_APP_END']:#010x};",
            f"BL_RAM_ORIGIN   = {ram['start']:#010x};",
            f"BL_RAM_LENGTH   = {ram['size']:#010x};",
            f"BL_STACK_SIZE   = {layout['stack_size']:#010x};",
            "",
        ]), encoding="utf-8", newline="\n")
    return Path(out_dir)


def build_bl_host(out: Path, layout_dir: Path, source: Path = ROOT, werror: bool = True,
                  rebuild: bool = False) -> Path:
    """Bootloader core with the simulated HAL (bootloader/test/host/bl_host.c) as DLL.

    Built against the checksum of its sources (checksum(), COM-09): an unchanged tree reuses the DLL, any
    change to a compiled source, to a header of bootloader/include, to the generated memory layout or to the
    compiler command line rebuilds it. `rebuild` forces the build."""
    bl = Path(source) / "bootloader"
    core = sorted((bl / "src" / "core").glob("*.c"))
    host = bl / "test" / "host" / "bl_host.c"
    # family hooks of the layout's family (BL-08): hal_app_entry_plausible() of bootloader/platforms/<family>
    platform, family_files = family_sources(source, layout_family(layout_dir))
    # BL-03: chip UID and key sector come from the host part (BL_HOST_BUILD); its default key sector is provisioned
    # with the insecure test key of the repository (one source: tools/canflash/keys)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import blkeys  # noqa: E402

    test_key = blkeys.parse_key(blkeys.TEST_KEY_FILE.read_text(encoding="utf-8"), str(blkeys.TEST_KEY_FILE))
    master_define = "-DBL_HOST_TEST_MASTER_KEY=" + ",".join(f"0x{b:02X}" for b in test_key)
    cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "x86_64-windows-gnu", "-shared", "-O1", "-std=c99",
           "-Wall", "-Wextra", *(["-Werror"] if werror else []), "-DBL_HOST_BUILD", master_define,
           f"-I{bl / 'include'}",
           f"-I{bl / 'src' / 'core'}", f"-I{platform}", f"-I{bl / 'platforms' / 'common'}", f"-I{layout_dir}",
           *map(str, core), str(host),
           str(HOST_SECURITY_SOURCE), "-o", str(out)]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sources = [*core, host, HOST_SECURITY_SOURCE, blkeys.TEST_KEY_FILE, *sorted((bl / "include").rglob("*.h")),
               *sorted((bl / "src" / "core").glob("*.h")), *sorted((bl / "src" / "core").glob("*.inc")),
               *family_files, *sorted(Path(layout_dir).rglob("*.h")), Path(__file__).resolve()]
    digest = checksum(sources, extra=[*cmd[1:-1], f"werror={werror}"])
    if rebuild:
        stamp_file(out).unlink(missing_ok=True)
    build_if_changed(out, digest, lambda: subprocess.run(cmd, check=True))
    return Path(out)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="build the bootloader host DLL for a project's memory layout")
    parser.add_argument("project", type=Path, nargs="?", help="projects/<project>/project.yaml (or --test-layout)")
    parser.add_argument("out", type=Path, help="DLL, e.g. build/host/bl_host.dll")
    parser.add_argument("--test-layout", default=None,
                        help="synthetic layout bootloader/test/layouts/<name>.yaml instead of a project")
    args = parser.parse_args()
    if (args.project is None) == (args.test_layout is None):
        parser.error("give either a project or --test-layout")
    out_gen = args.out.parent / f"{args.out.stem}_layout"
    layout = write_test_layout(args.test_layout, out_gen) if args.test_layout \
        else generate_layout(args.project, out_gen)
    print(build_bl_host(args.out, layout))
