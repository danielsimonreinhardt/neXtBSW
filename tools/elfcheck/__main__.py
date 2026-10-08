"""Command line of the image check (PLAT-80). See README.md.

  python tools/elfcheck --project projects/evalboard_s32k144_demo/project.yaml --build <build directory>
  python tools/elfcheck --mcudb mcudb/s32k1xx/S32K116.yaml --bootloader bootloader.elf --app app.elf
  python tools/elfcheck fcf --project projects/evalboard_s32k144_demo/project.yaml bootloader.elf bootloader.hex
  python tools/elfcheck timing --project projects/evalboard_s32k144_demo/project.yaml --build <build directory>
  python tools/elfcheck product --app <app.ecu.elf of a build with NX_PRODUCT_BUILD=ON>

Exit code 0 without errors (warnings are printed), 1 with errors, 2 for unusable input.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["timing"]:          # PLAT-82: RTE timing and interrupt priorities (timing.py)
        import timing
        return timing.main(argv[1:])
    if argv[:1] == ["fcf"]:             # NVM-15: flash configuration field of images that write sector 0
        return main_fcf(argv[1:])
    if argv[:1] == ["product"]:         # SYS-74: product image without test and fault-injection commands
        import product
        return product.main(argv[1:])
    if argv[:1] == ["blversion"]:       # BL-32: bootloader image changed -> BL_VERSION_NUMBER changed
        import blversion
        return blversion.main(argv[1:])
    parser = argparse.ArgumentParser(prog="elfcheck", description="Linked images against the derivative map and "
                                     "the core (tool-classification.md proposal D)")
    parser.add_argument("--project", type=Path, help="project.yaml (board, flash, bootloader and application RAM)")
    parser.add_argument("--mcudb", type=Path, help="derivative file mcudb/<family>/<derivative>.yaml "
                        "(default: the derivative of the project's board)")
    parser.add_argument("--flash", help="code flash region (default: memory.flash of the project or the only one)")
    parser.add_argument("--board", help="board name for the target identity '<board>/<derivative>'")
    parser.add_argument("--build", type=Path, help="CMake build directory: takes bootloader.elf, app.ecu.elf and "
                        "blupdater.elf from it")
    parser.add_argument("--bootloader", type=Path)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--updater", type=Path)
    parser.add_argument("--no-records", action="store_true", help="do not check the <stem>.record.hex files")
    parser.add_argument("--no-rom-list", action="store_true", help="the application has no ROM check list (SM-14)")
    parser.add_argument("--strict", action="store_true", help="warnings count as errors")
    args = parser.parse_args(argv)

    try:
        exp = check.load_expectation(args.project, args.mcudb, args.flash, args.board)
    except (OSError, ValueError, KeyError) as e:
        print(f"elfcheck: {e}", file=sys.stderr)
        return 2
    images = check.find_build_images(args.build) if args.build else {}
    for key in ("bootloader", "app", "updater"):
        if getattr(args, key):
            images[key] = getattr(args, key)
    if not images:
        print("elfcheck: no image given (--build, --bootloader, --app, --updater)", file=sys.stderr)
        return 2
    findings = check.check_images(exp, images.get("bootloader"), images.get("app"), images.get("updater"),
                                  records=not args.no_records, rom_list=not args.no_rom_list)
    print(f"elfcheck: {exp.derivative} ({exp.core}, flash {exp.flash.name}) from {exp.derivative_file.name}"
          + (f", project {exp.project_file}" if exp.project_file else ""))
    for key, path in images.items():
        print(f"  {key}: {path}")
    for finding in findings:
        print(finding)
    errors = len(findings.errors) + (len(findings) - len(findings.errors) if args.strict else 0)
    print(f"elfcheck: {len(findings.errors)} errors, {len(findings) - len(findings.errors)} warnings")
    return 1 if errors else 0


def main_fcf(argv: list[str]) -> int:
    """NVM-15: only the flash configuration field (post-build step of the bootloader, bootloader/CMakeLists.txt)."""
    parser = argparse.ArgumentParser(prog="elfcheck fcf", description="Flash configuration field of images that "
                                     "write the first code flash sector (NVM-15)")
    parser.add_argument("--project", type=Path, help="project.yaml (derivative of its board)")
    parser.add_argument("--mcudb", type=Path, help="derivative file mcudb/<family>/<derivative>.yaml")
    parser.add_argument("--flash", help="code flash region (default: memory.flash of the project or the only one)")
    parser.add_argument("--family", help="MCU family of the build (NX_FAMILY): a family without a configuration "
                        "field ends here with exit code 0, without reading project or derivative")
    parser.add_argument("images", type=Path, nargs="+", help="ELF or Intel hex files")
    args = parser.parse_args(argv)
    if args.family is not None and args.family not in check.CONFIG_FIELDS:
        return 0
    try:
        exp = check.load_expectation(args.project, args.mcudb, args.flash)
    except (OSError, ValueError, KeyError) as e:
        print(f"elfcheck fcf: {e}", file=sys.stderr)
        return 2
    errors = 0
    for image in args.images:
        findings = check.check_config_field_file(image, exp)
        for finding in findings:
            print(finding, file=sys.stderr)
        errors += len(findings.errors)
    if errors:
        print(f"elfcheck fcf: {errors} errors - an image that erases sector 0 must write the flash configuration "
              "field unsecured (NVM-15)", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
