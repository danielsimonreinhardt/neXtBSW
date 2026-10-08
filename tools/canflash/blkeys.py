"""Keys of the bootloader's security access on the workstation (BL-03).

The project names a master key (diagnostics.bootloader.security.key, generated into bl_security.h as
BL_SECURITY_KEY_NAME); the key itself never enters the repository, a build or an image. Where it is looked up:

1. environment variable NX_BL_KEY: 64 hex digits (CI, neXtBSW Studio) - used for whatever name the project gives
2. environment variable NX_BL_KEY_FILE: path of a key file
3. the insecure test key of the repository (keys/nextbsw-insecure-test.key), only under its own name
4. the key store: NX_BL_KEY_DIR or %LOCALAPPDATA%/neXtBSW/keys, file <name>.key

A key file holds the 32 bytes as 64 hex digits; lines starting with # are comments.

From the master key and the chip UID the tester derives the device key, HMAC-SHA256(master key, UID); the
bootloader holds only that (key record in the key sector, written by provision.py). The answer to a seed is
HMAC-SHA256(device key, seed). Standard library only (hmac, hashlib): the same calculation as the bootloader's own
SHA-256, which the host tests compare against both.
"""

import hashlib
import hmac
import os
import secrets
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
MASTER_KEY_LEN = 32
KEY_ENV = "NX_BL_KEY"
KEY_FILE_ENV = "NX_BL_KEY_FILE"
KEY_DIR_ENV = "NX_BL_KEY_DIR"
KEY_SUFFIX = ".key"
# Deliberately insecure master key for host tests and CI (tools/nxconfig/model.py INSECURE_TEST_KEY_NAME). A project
# must allow it (allow_insecure_test_key), provision.py refuses it otherwise.
TEST_KEY_NAME = "nextbsw-insecure-test"
TEST_KEY_FILE = HERE / "keys" / f"{TEST_KEY_NAME}{KEY_SUFFIX}"

# Methods of the security access: number in the bootloader (bl_config.h BL_SECURITY_METHOD_*, DID 0xF1A1)
METHOD_HMAC_SHA256 = 1
METHODS = {METHOD_HMAC_SHA256: "hmac-sha256"}
LEGACY_METHOD = "legacy-xor"            # bootloaders before BL-03, 4-byte seed and key

# XOR/rotl13 of the bootloaders before BL-03 (identification version below bl_config.h BL_SECURITY_HMAC_SINCE).
# Kept only for the transition, until no such bootloader is left in use: the value protected nothing, it is part of
# the published history of the repository and it is not a key of any newer bootloader.
LEGACY_XOR_CONSTANT = 0x5EC0B007

# Key record in the key sector, see bootloader/include/bl_config.h
KEY_RECORD_MAGIC = b"NXKY"
KEY_RECORD_VERSION = 1
KEY_RECORD_LEN = 48


class MasterKeyError(ValueError):
    """A master key that cannot be found or used; the message says where it was looked for."""


@dataclass(frozen=True)
class MasterKey:
    name: str
    key: bytes = b""
    source: str = ""
    insecure: bool = False              # the test key of the repository

    def __repr__(self) -> str:          # never print key material by accident
        return f"MasterKey({self.name!r}, source={self.source!r}, insecure={self.insecure})"


def key_dir() -> Path:
    """Key store of this workstation: NX_BL_KEY_DIR, else %LOCALAPPDATA%/neXtBSW/keys (~/.local/share elsewhere)."""
    if os.environ.get(KEY_DIR_ENV):
        return Path(os.environ[KEY_DIR_ENV])
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "share") / "neXtBSW" / "keys"


def parse_key(text: str, what: str) -> bytes:
    """32 bytes of a key file or of NX_BL_KEY: hex digits, # comments, whitespace ignored."""
    digits = "".join(line.split("#", 1)[0].strip() for line in text.splitlines()).replace(" ", "")
    try:
        key = bytes.fromhex(digits)
    except ValueError:
        raise MasterKeyError(f"{what}: not a hex key") from None
    if len(key) != MASTER_KEY_LEN:
        raise MasterKeyError(f"{what}: {len(key)} bytes instead of {MASTER_KEY_LEN}")
    return key


def load_master_key(name: str) -> MasterKey:
    """The master key named by the project (see the module description for the order)."""
    if os.environ.get(KEY_ENV):
        return MasterKey(name, parse_key(os.environ[KEY_ENV], f"environment variable {KEY_ENV}"),
                         f"environment variable {KEY_ENV}", False)
    if os.environ.get(KEY_FILE_ENV):
        path = Path(os.environ[KEY_FILE_ENV])
        if not path.is_file():
            raise MasterKeyError(f"{KEY_FILE_ENV}={path}: no such file")
        return MasterKey(name, parse_key(path.read_text(encoding="utf-8"), str(path)), str(path),
                         path.resolve() == TEST_KEY_FILE.resolve())
    if name == TEST_KEY_NAME:
        return MasterKey(name, parse_key(TEST_KEY_FILE.read_text(encoding="utf-8"), str(TEST_KEY_FILE)),
                         str(TEST_KEY_FILE), True)
    path = key_dir() / f"{name}{KEY_SUFFIX}"
    if not path.is_file():
        raise MasterKeyError(f"master key '{name}' not found: {path} does not exist and neither {KEY_ENV} nor "
                             f"{KEY_FILE_ENV} is set. Create it once with 'canflash keygen {name}' (and keep a "
                             "copy outside this PC: without it no device provisioned with it can be unlocked "
                             f"again), or copy the existing key file of this project to {path}")
    return MasterKey(name, parse_key(path.read_text(encoding="utf-8"), str(path)), str(path), False)


def create_master_key(name: str, directory: Path | None = None) -> Path:
    """Writes a new random master key <directory>/<name>.key (default: the key store); never overwrites one."""
    if name == TEST_KEY_NAME:
        raise MasterKeyError(f"{name} is the insecure test key of the repository, choose another name")
    target = Path(directory or key_dir()) / f"{name}{KEY_SUFFIX}"
    if target.exists():
        raise MasterKeyError(f"{target} exists already - a second key under the same name would lock out every "
                             "device provisioned with the first one")
    target.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(MASTER_KEY_LEN)
    with open(target, "x", encoding="utf-8", newline="\n") as f:   # "x": fails instead of overwriting
        f.write(f"# neXtBSW bootloader master key '{name}' (BL-03). Secret: do not commit, do not share.\n"
                "# Back it up outside this PC; devices provisioned with it cannot be unlocked without it.\n"
                f"{key.hex()}\n")
    return target


def device_key(master: bytes, uid: bytes) -> bytes:
    """Device key of a chip: HMAC-SHA256(master key, UID as DID 0xF18C reports it)."""
    return hmac.new(master, uid, hashlib.sha256).digest()


def seed_key(device: bytes, seed: bytes) -> bytes:
    """Answer to a seed of the bootloader: HMAC-SHA256(device key, seed), all 32 bytes."""
    return hmac.new(device, seed, hashlib.sha256).digest()


APP_KEY_LABEL = b"NXAK"                  # ecu/uds DeviceKeySecurity.cpp KEY_LABEL
APP_MAC_LEN = 32


def app_key(device: bytes, level: int, seed: bytes, key_bytes: int) -> bytes:
    """Answer to a seed of an application security level with method hmac-sha256 (COM-30): the first `key_bytes`
    of HMAC-SHA256(device key, "NXAK" || requestSeed || seed). Same device key as the bootloader's security access;
    label and level keep it from answering the bootloader or another level."""
    if not 1 <= key_bytes <= APP_MAC_LEN:
        raise ValueError(f"key of {key_bytes} bytes: method hmac-sha256 has 1..{APP_MAC_LEN}")
    return hmac.new(device, APP_KEY_LABEL + bytes([level]) + bytes(seed), hashlib.sha256).digest()[:key_bytes]


def legacy_key(seed: int) -> int:
    """Answer of the bootloaders before BL-03: XOR with LEGACY_XOR_CONSTANT, rotated left by 13 bits."""
    x = seed ^ LEGACY_XOR_CONSTANT
    return ((x << 13) | (x >> 19)) & 0xFFFFFFFF


def key_record(device: bytes, method: int = METHOD_HMAC_SHA256) -> bytes:
    """Content of the key sector (bootloader/include/bl_config.h): magic, version, method, key length, key, CRC-32."""
    head = KEY_RECORD_MAGIC + bytes([KEY_RECORD_VERSION, method, len(device), 0xFF]) + device
    record = head + struct.pack("<I", zlib.crc32(head))
    return record + b"\xff" * (KEY_RECORD_LEN - len(record))
