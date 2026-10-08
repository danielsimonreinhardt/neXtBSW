# SPDX-License-Identifier: Apache-2.0
"""Command line of the host side of the CSEc way back to the factory state (NVM-13). See README.md.

  python -m tools.csec load-master --key-file <file> [--uid <30 hex>]
        M1, M2, M3 for the first CMD_LOAD_KEY of the MASTER_ECU_KEY, the M4/M5 the device must answer, and the line
        for the device console (typed confirmation included)
  python -m tools.csec check-load --key-file <file> --m4 <64 hex> --m5 <32 hex> [--uid <30 hex>]
        the device's answer against the expectation
  python -m tools.csec debug-auth --key-file <file> --challenge <32 hex> --uid <30 hex>
        AUTHORIZATION for CMD_DBG_AUTH and the line for the device console

The key file holds the MASTER_ECU_KEY as 32 hex digits and must lie OUTSIDE the repository (never in plain text in
the repository, NVM-14). Instead of --key-file, --key-name <name> or --project <project.yaml> take it from the key
store of the workstation (tools/nxconfig/keystore.py, %LOCALAPPDATA%/neXtBSW/keys/csec/<name>.key; created with
`python tools/nxconfig csec-key new <project>`). There is deliberately no option to give the key on the command line (shell history) and no
option for key flags: no key ever gets WRITE_PROTECTION.

Nothing here talks to a device. The console lines are for the command of the device side (bspCsec, wired into a
test build by NVM-07); running them on a board is NVM-07, with the user, never unattended.

Exit code 0 ok, 1 answer does not match, 2 unusable input.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.csec import she

ROOT = Path(__file__).resolve().parents[2]

LOAD_CONFIRM = "LOADKEY-MASTER-ECU-KEY"   # CsecRecovery::LOAD_KEY_CONFIRMATION
AUTH_CONFIRM_PREFIX = "DBGAUTH-"          # CsecRecovery: "DBGAUTH-" + first 4 challenge bytes in upper-case hex
CONSOLE = "csec"                          # console command group proposed for NVM-07 (see README.md)
CONSOLE_LINE_MAX = 127                    # OpenBSW StdioConsoleInput keeps a line in etl::string<128>


def auth_confirm(challenge: bytes) -> str:
    """The typed confirmation of CMD_DBG_AUTH: binds the line to the challenge of this session."""
    return AUTH_CONFIRM_PREFIX + bytes(challenge[:4]).hex().upper()


class InputError(Exception):
    pass


def _hex(text: str, length: int, what: str) -> bytes:
    cleaned = text.strip().replace(" ", "").replace("_", "")
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    try:
        value = bytes.fromhex(cleaned)
    except ValueError:
        raise InputError(f"{what}: not hex") from None
    if len(value) != length:
        raise InputError(f"{what}: {len(value)} bytes, expected {length}")
    return value


def _keystore():
    """NVM-14: key store of the workstation (tools/nxconfig/keystore.py), imported the way the tools import nxconfig."""
    tools = str(ROOT / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from nxconfig import keystore
    return keystore


def read_key(path: Path) -> bytes:
    """A MASTER_ECU_KEY file outside the repository, in the format of the key store (NVM-14: # comments, 32 hex
    digits, public test values and the empty slot refused)."""
    keystore = _keystore()
    path = path.resolve()
    try:
        path.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise InputError(f"{path}: key file inside the repository - keep the MASTER_ECU_KEY outside it (NVM-14)")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"{path}: {exc.strerror or exc}") from None
    try:
        return keystore.parse_key(text, f"{path.name}: MASTER_ECU_KEY")
    except keystore.KeyStoreError as exc:
        raise InputError(str(exc)) from None


def master_key(args) -> bytes:
    """The MASTER_ECU_KEY: --key-file, else from the key store of the workstation by --key-name, else by the name the
    project gives (project.yaml security.master_ecu_key, default the board name)."""
    if args.key_file is not None:
        return read_key(args.key_file)
    keystore = _keystore()
    name = args.key_name
    if name is None:
        import nxconfig
        from nxconfig import security
        project = args.project / "project.yaml" if args.project.is_dir() else args.project
        config, messages = nxconfig.check(project)
        if config is None:
            raise InputError(f"{project}: not usable - " + "; ".join(str(m) for m in messages
                                                                   if m.severity == "error"))
        name = security.master_ecu_key_name(config)
    try:
        return keystore.load_master_ecu_key(name).key
    except keystore.KeyStoreError as exc:
        raise InputError(str(exc)) from None


def _load(args) -> tuple[she.KeyUpdate, bytes]:
    uid = _hex(args.uid, 15, "--uid") if args.uid else she.WILDCARD_UID
    return she.first_master_key_load(master_key(args), uid), uid


def cmd_load_master(args) -> int:
    update, uid = _load(args)
    print("first CMD_LOAD_KEY of the MASTER_ECU_KEY (slot 1, authorised by itself = all ones, counter 1, NO flags)")
    print(f"  UID in M1: {'0 (wildcard)' if uid == she.WILDCARD_UID else uid.hex().upper()}")
    for name in ("m1", "m2", "m3"):
        print(f"  {name.upper()} = {getattr(update, name).hex().upper()}")
    print("expected answer of the device:")
    print(f"  M4 = {update.m4.hex().upper()}")
    print(f"  M5 = {update.m5.hex().upper()}")
    if uid == she.WILDCARD_UID:
        print("  (with a wildcard M1 the device answers with its own UID in M4 - check with check-load --uid <UID>)")
    print("device console (test build of NVM-07, user at the board only; one line each, M1..M3 staged first):")
    for line in load_lines(update):
        print(f"  {line}")
    return 0


def load_lines(update: she.KeyUpdate) -> list[str]:
    """Console lines of the load. Staged, because a console line holds at most CONSOLE_LINE_MAX characters
    (OpenBSW StdioConsoleInput: etl::string<128>) and M1..M3 with the word are 166."""
    return [f"{CONSOLE} m1 {update.m1.hex().upper()}", f"{CONSOLE} m2 {update.m2.hex().upper()}",
            f"{CONSOLE} m3 {update.m3.hex().upper()}", f"{CONSOLE} loadkey {LOAD_CONFIRM}"]


def cmd_check_load(args) -> int:
    update, _ = _load(args)
    problems = she.check_answer(update, _hex(args.m4, 32, "--m4"), _hex(args.m5, 16, "--m5"))
    for problem in problems:
        print(f"MISMATCH: {problem}")
    if not problems:
        print("M4/M5 as expected: the MASTER_ECU_KEY is loaded as computed (counter 1, no flags)")
    return 1 if problems else 0


def cmd_debug_auth(args) -> int:
    key = master_key(args)
    challenge = _hex(args.challenge, 16, "--challenge")
    uid = _hex(args.uid, 15, "--uid")
    auth = she.debug_authorization(key, challenge, uid)
    print("CMD_DBG_AUTH: AUTHORIZATION = CMAC_K(CHALLENGE | UID), K = KDF(MASTER_ECU_KEY, DEBUG_KEY_C)")
    print(f"  AUTHORIZATION = {auth.hex().upper()}")
    print("device console - ERASES ALL CSEc KEYS AND THE FLEXNVM PARTITION, then reset (user at the board only):")
    print(f"  {auth_line(auth, challenge)}")
    return 0


def auth_line(auth: bytes, challenge: bytes) -> str:
    return f"{CONSOLE} dbgauth {auth.hex().upper()} {auth_confirm(challenge)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="csec", allow_abbrev=False,
                                     description="Host side of the CSEc way back to the factory state (NVM-13): SHE "
                                     "KDF, M1..M5, debug authorisation. No device access.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("load-master", "M1..M3 and expected M4/M5 of the first MASTER_ECU_KEY load"),
                       ("check-load", "the device's M4/M5 against the expectation"),
                       ("debug-auth", "AUTHORIZATION of CMD_DBG_AUTH")):
        p = sub.add_parser(name, help=text, allow_abbrev=False)  # "--key" must not mean --key-file
        source = p.add_mutually_exclusive_group(required=True)
        source.add_argument("--key-file", type=Path,
                            help="MASTER_ECU_KEY as 32 hex digits, outside the repository")
        # NVM-14: the key store of the workstation (%LOCALAPPDATA%/neXtBSW/keys/csec/<name>.key, NX_CSEC_KEY_DIR)
        source.add_argument("--project", type=Path,
                            help="project.yaml or project folder: the key named there (security.master_ecu_key, "
                                 "default the board name) from the key store of the workstation")
        source.add_argument("--key-name", help="name of the key in the key store of the workstation")
        if name == "debug-auth":
            p.add_argument("--challenge", required=True, help="CHALLENGE of CMD_DBG_CHAL, 32 hex digits")
            p.add_argument("--uid", required=True, help="UID of CMD_GET_ID, 30 hex digits")
        else:
            p.add_argument("--uid", help="UID of CMD_GET_ID, 30 hex digits (default: 0, wildcard)")
        if name == "check-load":
            p.add_argument("--m4", required=True, help="M4 of the device, 64 hex digits")
            p.add_argument("--m5", required=True, help="M5 of the device, 32 hex digits")
    args = parser.parse_args(argv)
    try:
        return {"load-master": cmd_load_master, "check-load": cmd_check_load,
                "debug-auth": cmd_debug_auth}[args.command](args)
    except InputError as exc:
        print(f"csec: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
