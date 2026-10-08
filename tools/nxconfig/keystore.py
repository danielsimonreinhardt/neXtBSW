# SPDX-License-Identifier: Apache-2.0
"""NVM-14: MASTER_ECU_KEY of development boards in the key store of the workstation, and the check that no key
material enters the repository.

Why a file outside the repository (and not an environment variable or a file in the tree): the MASTER_ECU_KEY is
the only way back from a partition with key space (debug authentication, doc/architecture/csec-concept.md 6.3), so
it has to survive reboots, shells and worktrees and must be found by every tool - but it must never be committed.
A file in the per-user application data folder is outside every worktree (no .gitignore rule can be forgotten), is
protected by the user account, is the same place as the bootloader master keys of BL-03 (tools/canflash/blkeys.py)
and can be backed up as one folder. An environment variable holding the key would end up in child processes, logs
and shell histories; only the *folder* can be moved by one (NX_CSEC_KEY_DIR, for tests and CI).

Stable interface for the host tools (NVM-13: KDF, M1-M5, DBG_AUTH), standard library only:

    key_dir() -> Path                            NX_CSEC_KEY_DIR, else %LOCALAPPDATA%/neXtBSW/keys/csec
    key_path(name, directory=None) -> Path       <key_dir>/<name>.key
    load_master_ecu_key(name, directory=None) -> StoredKey   (.key: 16 bytes; repr never shows it)
    create_master_ecu_key(name, directory=None) -> Path      new random key, never overwrites
    checked_flags(flags) -> tuple[str, ...]      flags in SHE order; raises WriteProtectionRefused

The name comes from the project: security.master_ecu_key_name(cfg) (project.yaml security.master_ecu_key, default the
board name). File format of <name>.key, UTF-8: lines starting with # are comments, the rest is 32 hex digits
(whitespace allowed) = the 16 key bytes in the order they are loaded (SHE: most significant byte first).
All-zero, all-one (the empty slot) and the public SHE/AES test values are refused: they are no project secret.

scan(root) looks for key material in the files git would commit (tracked plus untracked, not ignored): every key
of the key store in hex (any case, with or without 0x, separators) or as raw bytes, key files (*.key) outside the
allow list, and a MASTER_ECU_KEY written out next to 32 hex digits.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

KEY_LEN = 16
KEY_DIR_ENV = "NX_CSEC_KEY_DIR"
KEY_SUFFIX = ".key"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HEX_RUN_RE = re.compile(r"[0-9A-Fa-f]{32}")

# Public values: never a project secret (empty slot, SHE specification and FIPS-197/SP 800-38B test vectors).
PUBLIC_KEYS = frozenset(bytes.fromhex(h) for h in (
    "00" * 16, "ff" * 16,
    "000102030405060708090a0b0c0d0e0f",         # SHE specification example MASTER_ECU_KEY
    "0f0e0d0c0b0a09080706050403020100",         # SHE specification example new key
    "2b7e151628aed2a6abf7158809cf4f3c",         # AES/CMAC test key (FIPS-197, SP 800-38B)
))

# SHE key flags in the order of the flag field of M2 (the packing into bits belongs to the tool that builds M2).
# WRITE_PROTECTION is not in the list and never will be (NVM-14).
SHE_FLAGS = ("boot_protection", "debugger_protection", "key_usage", "wildcard", "verify_only")

# Key files that are allowed in the repository: the deliberately insecure test key of the bootloader (BL-03).
ALLOWED_KEY_FILES = frozenset({"tools/canflash/keys/nextbsw-insecure-test.key"})
SCAN_LIMIT = 64 * 1024 * 1024
LITERAL_RE = re.compile(r"master[\s_-]*ecu[\s_-]*key", re.IGNORECASE)


class KeyStoreError(ValueError):
    """A key that cannot be found or used; the message says where it was looked for."""


class WriteProtectionRefused(ValueError):
    """A key was to get WRITE_PROTECTION - refused by every tool of neXtBSW (NVM-14)."""


@dataclass(frozen=True)
class StoredKey:
    name: str
    key: bytes
    path: Path

    def __repr__(self) -> str:          # never print key material by accident
        return f"StoredKey({self.name!r}, path={str(self.path)!r})"

    @property
    def fingerprint(self) -> str:
        """Short, non-reversible label for logs: the first 8 hex digits of SHA-256 over the key."""
        import hashlib
        return hashlib.sha256(b"nextbsw-csec-fingerprint" + self.key).hexdigest()[:8]


def key_dir() -> Path:
    """Key store of the MASTER_ECU_KEYs on this workstation."""
    if os.environ.get(KEY_DIR_ENV):
        return Path(os.environ[KEY_DIR_ENV])
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "share") / "neXtBSW" / "keys" / "csec"


def check_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise KeyStoreError(f"key name {name!r}: letters, digits, '.', '_', '-', at most 64 characters")
    if HEX_RUN_RE.search(name):
        raise KeyStoreError(f"key name {name!r} looks like key material - name the key, never write it out")
    return name


def key_path(name: str, directory: Path | None = None) -> Path:
    return Path(directory or key_dir()) / f"{check_name(name)}{KEY_SUFFIX}"


def parse_key(text: str, what: str) -> bytes:
    """The 16 bytes of a key file: # comments and whitespace ignored, 32 hex digits."""
    digits = "".join(line.split("#", 1)[0] for line in text.splitlines())
    digits = "".join(digits.split())
    try:
        key = bytes.fromhex(digits)
    except ValueError:
        raise KeyStoreError(f"{what}: not 32 hex digits") from None
    if len(key) != KEY_LEN:
        raise KeyStoreError(f"{what}: {len(key)} bytes instead of {KEY_LEN}")
    if key in PUBLIC_KEYS:
        raise KeyStoreError(f"{what}: a public test value or an empty slot is no MASTER_ECU_KEY")
    return key


def load_master_ecu_key(name: str, directory: Path | None = None) -> StoredKey:
    path = key_path(name, directory)
    if not path.is_file():
        raise KeyStoreError(f"MASTER_ECU_KEY '{name}' not found: {path} does not exist. Create it once with "
                            f"'nxconfig csec-key new <project.yaml>' and keep a copy outside this PC - without it "
                            f"no board loaded with it can be brought back to the factory state")
    return StoredKey(name, parse_key(path.read_text(encoding="utf-8"), str(path)), path)


def create_master_ecu_key(name: str, directory: Path | None = None) -> Path:
    """Writes a new random key <directory>/<name>.key; never overwrites (a second key under the same name would lock
    out every board loaded with the first one)."""
    target = key_path(name, directory)
    if target.exists():
        raise KeyStoreError(f"{target} exists already - keep it; a second key under the same name would lock out "
                            f"every board loaded with the first one")
    target.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_LEN)
    while key in PUBLIC_KEYS:                       # practically never
        key = secrets.token_bytes(KEY_LEN)
    with open(target, "x", encoding="utf-8", newline="\n") as f:     # "x": fails instead of overwriting
        f.write(f"# neXtBSW CSEc MASTER_ECU_KEY '{name}' of development boards (NVM-14). Secret: never commit, "
                "never paste into project files.\n"
                "# Back it up outside this PC: boards loaded with it can only be brought back with it.\n"
                f"{key.hex()}\n")
    return target


def stored_keys(directory: Path | None = None) -> dict[str, bytes]:
    """All readable keys of the key store by name (unreadable files are skipped - scan() says how many)."""
    folder = Path(directory or key_dir())
    out: dict[str, bytes] = {}
    if folder.is_dir():
        for path in sorted(folder.glob(f"*{KEY_SUFFIX}")):
            try:
                out[path.stem] = parse_key(path.read_text(encoding="utf-8"), str(path))
            except (OSError, KeyStoreError):
                continue
    return out


def checked_flags(flags: Iterable[str]) -> tuple[str, ...]:
    """Flags of a key in SHE order. WRITE_PROTECTION in any spelling raises WriteProtectionRefused, an unknown flag
    KeyStoreError. Every tool that builds a LOAD_KEY message calls this with the flags it is about to use."""
    from .model import WRITE_PROTECTION_REFUSED, is_write_protection
    given = list(flags)
    if any(is_write_protection(f) for f in given):
        raise WriteProtectionRefused(WRITE_PROTECTION_REFUSED)
    unknown = [f for f in given if f not in SHE_FLAGS]
    if unknown:
        raise KeyStoreError(f"unknown key flag(s) {', '.join(map(str, unknown))} (known: {', '.join(SHE_FLAGS)})")
    return tuple(f for f in SHE_FLAGS if f in given)


# ------------------------------------------------------------------------------------------------ leak scan


@dataclass(frozen=True)
class Finding:
    path: str                           # repository relative, posix
    line: int | None
    text: str

    def __str__(self) -> str:
        return f"{self.path}{f':{self.line}' if self.line else ''}: error: {self.text}"


def candidate_files(root: Path) -> list[str]:
    """Files git would commit: tracked plus untracked that are not ignored (a key about to be added counts)."""
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                       capture_output=True)
    if r.returncode != 0:
        raise KeyStoreError(f"git ls-files in {root} failed: {r.stderr.decode(errors='replace').strip()}")
    return sorted({p for p in r.stdout.decode("utf-8", errors="replace").split("\0") if p})


def _hex_stream(text: str) -> str:
    return re.sub(r"0x|[^0-9a-f]", "", text.lower())


def scan(root: Path, keys: dict[str, bytes] | None = None, files: Iterable[str] | None = None) -> list[Finding]:
    """Key material in the files of `root` (default: every file git would commit). `keys`: the keys to look for by
    name (default: every key of the key store)."""
    root = Path(root)
    keys = stored_keys() if keys is None else keys
    wanted = {name: (key, key.hex()) for name, key in keys.items() if key not in PUBLIC_KEYS}
    out: list[Finding] = []
    for rel in (candidate_files(root) if files is None else files):
        rel = rel.replace("\\", "/")
        full = root / rel
        if not full.is_file():                     # submodule gitlinks, deleted files
            continue
        if rel.lower().endswith(KEY_SUFFIX) and rel not in ALLOWED_KEY_FILES:
            out.append(Finding(rel, None, "key file in the repository - key files belong in the key store of the "
                                          "workstation (tools/nxconfig/keystore.py), never in the tree"))
        try:
            if full.stat().st_size > SCAN_LIMIT:
                continue
            data = full.read_bytes()
        except OSError:
            continue
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        for name, (key, digits) in wanted.items():
            if key in data:
                out.append(Finding(rel, None, f"contains the key '{name}' of the key store as raw bytes"))
                continue
            if digits not in _hex_stream(text):
                continue
            line = next((i for i, ln in enumerate(lines, 1) if digits in _hex_stream(ln)), None)
            out.append(Finding(rel, line, f"contains the key '{name}' of the key store in hex"
                                          + ("" if line else " (spread over several lines)")))
        for i, ln in enumerate(lines, 1):
            if not LITERAL_RE.search(ln):
                continue
            for run in HEX_RUN_RE.findall(ln):
                if bytes.fromhex(run) not in PUBLIC_KEYS:
                    out.append(Finding(rel, i, "a MASTER_ECU_KEY is written out next to 32 hex digits - keys belong "
                                               "in the key store of the workstation"))
                    break
    return out
