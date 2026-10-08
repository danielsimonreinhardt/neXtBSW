"""Provision the device key of the bootloader's security access over the debugger (BL-03).

Once per device, with the bootloader image that is the same for every device:

1. chip UID: --uid <hex>, --uid-from-can (DID 0xF18C of a bootloader from BL-03 on, needs the CAN adapter) or
   --uid-from-jlink (reads the UID registers of the derivative with J-Link Commander)
2. device key = HMAC-SHA256(master key of the project, UID); master key from the key store of the workstation
   (tools/canflash/blkeys.py), never from the repository - the insecure test key only if the project allows it
3. key record (bootloader/include/bl_config.h) as an Intel hex file at BL_KEY_ADDR (the key sector), plus a J-Link
   Commander script that connects, writes exactly that sector (loadfile erases only the sectors the file touches),
   compares it (verifybin) and restarts the MCU
4. with --run the script is executed; without, the files are only written (dry run) and the command is printed

Afterwards `canflash info` shows "device key provisioned", and flashing over CAN unlocks with the derived key.
The hex file holds the device key of this one chip: it is written to a temporary directory by default and deleted
after --run unless --keep is given.

    .venv/Scripts/python tools/canflash/provision.py --project projects/<project>/project.yaml --uid-from-jlink --run
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import blkeys  # noqa: E402
import flashguard  # noqa: E402  (NVM-15: no mass erase, no sector 0 without its configuration field)

ROOT = HERE.parents[1]
JLINK_DEFAULT_PATHS = ("C:/Program Files/SEGGER/JLink/JLink.exe", "C:/Program Files (x86)/SEGGER/JLink/JLink.exe")


class ProvisionError(ValueError):
    pass


@dataclass(frozen=True)
class Plan:
    """What gets written: the key record at the key sector of one chip."""

    project: Path
    key_name: str
    key_source: str
    uid: bytes
    key_address: int
    key_area_size: int
    record: bytes
    jlink_device: str
    interface: str
    speed_khz: str
    uid_address: int
    uid_len: int


def check_key(key: "blkeys.MasterKey", allowed: bool) -> None:
    """The insecure test key of the repository must never reach a device unless the project says so."""
    if key.insecure and not allowed:
        raise ProvisionError(f"master key from {key.source} is the insecure test key of the repository: anybody can "
                             "compute the device key. Provisioning refused (the project does not set "
                             "diagnostics.bootloader.security.allow_insecure_test_key)")


def _project(project: Path):
    sys.path.insert(0, str(ROOT / "tools" / "bswgen"))
    import blgen  # noqa: E402
    from nxconfig.config import build_settings  # noqa: E402

    try:
        config = blgen.nxconfig.load(Path(project))
    except blgen.nxconfig.ConfigErrors as e:
        raise ProvisionError(f"{project}: {e}") from None
    if config.memory is None or config.diagnostics is None or config.memory.key_address is None:
        raise ProvisionError(f"{project}: no bootloader memory partitioning with a key sector (memory, diagnostics)")
    settings, _ = build_settings(config)
    return blgen, config, settings


def project_security(project: Path) -> dict:
    """Key sector, UID and key name of a project, and how the debugger reaches its MCU."""
    blgen, config, settings = _project(project)
    plan = config.diagnostics
    values = blgen.security_values(plan)
    return {"key_name": plan.key_name, "allow_test_key": plan.allow_test_key,
            "key_address": config.memory.key_address, "key_area_size": config.memory.key_size,
            "uid_address": values["BL_UID_ADDR"], "uid_len": values["BL_UID_LEN"],
            "jlink_device": settings["NX_JLINK_DEVICE"] or config.derivative.derivative.name,
            "interface": settings["NX_DEBUG_INTERFACE"] or "SWD", "speed_khz": settings["NX_DEBUG_SPEED_KHZ"],
            "probe_kind": settings["NX_DEBUG_PROBE_KIND"]}


def plan_for_project(project: Path, uid: bytes, key: "blkeys.MasterKey | None" = None) -> Plan:
    info = project_security(project)
    if len(uid) != info["uid_len"]:
        raise ProvisionError(f"UID of {len(uid)} bytes, the derivative of {project} has {info['uid_len']} "
                             "(mcudb uid.size)")
    if uid in (bytes(len(uid)), b"\xff" * len(uid)):
        raise ProvisionError(f"UID {uid.hex()} is no chip UID (all bits equal): wrong address or no connection")
    key = key or blkeys.load_master_key(info["key_name"])
    check_key(key, info["allow_test_key"])
    record = blkeys.key_record(blkeys.device_key(key.key, uid))
    return Plan(Path(project), key.name, key.source, uid, info["key_address"], info["key_area_size"], record,
                info["jlink_device"], info["interface"], info["speed_khz"], info["uid_address"], info["uid_len"])


# PLAT-86: every script of this tool ends with the application running (r, g, exit). Without this command the J-Link
# clears DEMCR.TRCENA when it closes the session and stops the DWT cycle counter of the application
# (doc/hardware/debug-session-close.md). The value is required ("Expected a dec value" without it); J-Link V6.44b+.
SKIP_DEBUG_DEINIT = "exec SetSkipDebugDeInit = 1\n"


def _jlink_head(device: str, interface: str, speed: str) -> str:
    # never -NoGui, 1000 kHz by default (CLAUDE.md, J-Link rules); connect first, the script stops on an error
    text = f"device {device}\nsi {interface}\nspeed {speed}\n"
    if interface == "JTAG":
        text += "jtagconf -1,-1\n"
    return text + "connect\n" + SKIP_DEBUG_DEINIT


def uid_script(device: str, interface: str, speed: str, address: int, length: int) -> str:
    """J-Link Commander script that reads the UID words (connect halts the MCU; r and g let it run again)."""
    return _jlink_head(device, interface, speed) + f"mem32 0x{address:08X}, {length // 4}\nr\ng\nexit\n"


def uid_from_jlink(output: str, address: int, length: int) -> bytes:
    """UID bytes from the mem32 output of J-Link Commander ("40048054 = 00112233 44556677 ..."): words in address
    order, each most significant byte first - the order of DID 0xF18C (seedkey_uid() of the bootloader)."""
    found = re.search(rf"^\s*0*{address:X}\s*=\s*((?:[0-9A-Fa-f]{{8}}\s*){{{length // 4}}})", output,
                      re.MULTILINE | re.IGNORECASE)
    if found is None:
        raise ProvisionError(f"no UID in the J-Link output (mem32 0x{address:08X}): connection failed or the "
                             "license dialog is waiting?\n" + output[-800:])
    return bytes.fromhex("".join(found.group(1).split()))


def hex_text(address: int, data: bytes) -> str:
    from intelhex import IntelHex
    import io

    ih = IntelHex()
    ih.frombytes(data, offset=address)
    out = io.StringIO()
    ih.write_hex_file(out)
    return out.getvalue()


def write_files(plan: Plan, out_dir: Path) -> dict[str, Path]:
    """<out>/keyrecord.hex and .bin (the device key of this chip!) and <out>/provision.jlink."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hexfile = out_dir / "keyrecord.hex"
    hexfile.write_text(hex_text(plan.key_address, plan.record), encoding="ascii", newline="\n")
    binfile = out_dir / "keyrecord.bin"
    binfile.write_bytes(plan.record)
    script = out_dir / "provision.jlink"
    # loadfile erases and programs only the sector the hex file touches (the key sector); verifybin compares the
    # flash with the record again, and with -ExitOnError a difference ends the run with an error
    script.write_text(_jlink_head(plan.jlink_device, plan.interface, plan.speed_khz)
                      + f'loadfile "{hexfile.as_posix()}"\n'
                      + f'verifybin "{binfile.as_posix()}", 0x{plan.key_address:08X}\nr\ng\nexit\n',
                      encoding="ascii", newline="\n")
    return {"hex": hexfile, "bin": binfile, "script": script}


def jlink_exe() -> str:
    for candidate in (os.environ.get("NX_JLINK_BIN"), shutil.which("JLink"), shutil.which("JLink.exe"),
                      *JLINK_DEFAULT_PATHS):
        if candidate and Path(candidate).is_file():
            return str(candidate)
        if candidate and (Path(candidate) / "JLink.exe").is_file():
            return str(Path(candidate) / "JLink.exe")
    raise ProvisionError("J-Link Commander not found (PATH, NX_JLINK_BIN or C:/Program Files/SEGGER/JLink)")


def guard_script(script: Path, project: Path, images: tuple[Path, ...] = ()) -> None:
    """NVM-15: the script and the files it writes, checked before J-Link runs it: no mass erase on a part that may be
    CSEc-partitioned, nothing that leaves sector 0 without its flash configuration field."""
    try:
        flashguard.check_debug_flash(Path(script).read_text(encoding="ascii"),
                                     [(p.name, flashguard.image_of_hex(p)) for p in images],
                                     flashguard.target_of_project(Path(project)))
    except flashguard.GuardError as e:
        raise ProvisionError(str(e)) from None


def run_jlink(script: Path, serial: str | None = None) -> str:
    cmd = [jlink_exe(), *(["-USB", serial] if serial else []), "-ExitOnError", "1", "-CommandFile", str(script)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise ProvisionError(f"J-Link failed ({result.returncode}):\n{result.stdout[-1500:]}{result.stderr[-500:]}")
    return result.stdout


def uid_over_can(args) -> bytes:
    import canflash

    canflash.select_layout(None, args.project, need_layout=False)
    timing = canflash.timing_from_project(Path(args.project))
    with canflash.bus_for(args.interface, args.channel, timing) as bus:
        if not canflash.reach_bootloader(bus, args.wait):
            raise ProvisionError("no answer from the bootloader")
        with canflash.make_client(bus) as client:
            uid = canflash.read_uid(client)
    if uid is None:
        raise ProvisionError("the bootloader does not report a chip UID (DID 0xF18C): it is older than BL-03 - "
                             "update it first, or use --uid-from-jlink")
    return uid


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="projects/<project>/project.yaml")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--uid", help="chip UID as DID 0xF18C reports it (hex)")
    source.add_argument("--uid-from-can", action="store_true", help="read DID 0xF18C over CAN (bootloader >= BL-03)")
    source.add_argument("--uid-from-jlink", action="store_true", help="read the UID registers with J-Link")
    parser.add_argument("--run", action="store_true", help="execute the J-Link script (default: write files only)")
    parser.add_argument("--out", default=None, help="directory for key record and script (default: temporary)")
    parser.add_argument("--keep", action="store_true", help="keep the key record hex after --run")
    parser.add_argument("--serial", default=None, help="J-Link serial number (several probes)")
    parser.add_argument("--interface", default="vector", help="CAN interface for --uid-from-can")
    parser.add_argument("--channel", default=0, type=int)
    parser.add_argument("--wait", default=0.0, type=float)
    args = parser.parse_args(argv)
    try:
        info = project_security(Path(args.project))
        if info["probe_kind"] not in ("", "jlink") and (args.run or args.uid_from_jlink):
            raise ProvisionError(f"the board's debug probe is {info['probe_kind']}; this tool drives J-Link only")
        out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="nx_provision_"))
        if args.uid:
            uid = bytes.fromhex(args.uid)
        elif args.uid_from_can:
            uid = uid_over_can(args)
        else:
            script = out / "uid.jlink"
            out.mkdir(parents=True, exist_ok=True)
            script.write_text(uid_script(info["jlink_device"], info["interface"], info["speed_khz"],
                                         info["uid_address"], info["uid_len"]), encoding="ascii", newline="\n")
            guard_script(script, Path(args.project))
            uid = uid_from_jlink(run_jlink(script, args.serial), info["uid_address"], info["uid_len"])
        plan = plan_for_project(Path(args.project), uid)
        files = write_files(plan, out)
        print(f"Chip UID {uid.hex()}, master key '{plan.key_name}' from {plan.key_source}")
        print(f"Key record for 0x{plan.key_address:08X} (key sector, {plan.key_area_size} bytes): {files['hex']}")
        if not args.run:
            print(f"Dry run. To write it: JLink -ExitOnError 1 -CommandFile {files['script']}  (or --run)")
            return 0
        guard_script(files["script"], Path(args.project), (files["hex"],))
        try:
            run_jlink(files["script"], args.serial)
        finally:
            if not args.keep:
                files["hex"].unlink(missing_ok=True)
                files["bin"].unlink(missing_ok=True)
        print("Device key provisioned; check with: canflash info --project " + str(args.project))
        return 0
    except (ProvisionError, blkeys.MasterKeyError, ValueError, subprocess.TimeoutExpired) as e:
        print(f"provision: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
