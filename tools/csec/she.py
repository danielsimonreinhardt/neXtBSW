# SPDX-License-Identifier: Apache-2.0
"""SHE key handling on the host for the CSEc way back to the factory state (NVM-13). See README.md.

Everything the workstation computes for the CSEc of the S32K1 family; the device only moves the results through the
CSEc parameter RAM (platforms/s32k1xx/bsp/bspCsec). No device access here.

  AES-128 (encryption only), AES-CBC, AES-CMAC      FIPS-197, NIST SP 800-38A, NIST SP 800-38B / RFC 4493
  Miyaguchi-Preneel compression, KDF               SHE: KDF(K, C) = AES-MP(K | C)
  memory update protocol M1..M5                    SHE: CMD_LOAD_KEY
  debug authorisation                              SHE: CMD_DBG_CHAL/CMD_DBG_AUTH, AUTH = CMAC_K(CHALLENGE | UID),
                                                   K = KDF(MASTER_ECU_KEY, DEBUG_KEY_C)

Sources: HIS SHE Functional Specification v1.1 (memory update protocol, key derivation, debug authentication, as
named by [AN5401] Appendix A and section 4.5), NXP AN5401 Rev. 1 "Getting Started with CSEc Security Module"
(doc/hardware/s32k1xx/AN5401.pdf) and the S32K1xx Reference Manual Rev. 13, 36.5.13 ([RM13]).

SAFETY RULE (CLAUDE.md, NVM-13): no key ever gets WRITE_PROTECTION. This module has no way to set any key flag: the
flag field of M2 is always zero and there is no parameter for it. A key with WRITE_PROTECTION removes the way back
for good ([RM13] 36.5.13.27, [AN5401] 4.5).

Pure Python on purpose: the host computes a handful of blocks, and a crypto package would be a new pinned dependency
of the shared environment for that. The primitives are checked against the published FIPS/NIST/RFC vectors and the
SHE example of the memory update protocol (test_she.py).
"""

from __future__ import annotations

from dataclasses import dataclass

BLOCK = 16

# SHE constants (already padded for the MP compression: data, bit 1, zeros, 0xB0 = 176 bits of message)
KEY_UPDATE_ENC_C = bytes.fromhex("010153484500800000000000000000B0")
KEY_UPDATE_MAC_C = bytes.fromhex("010253484500800000000000000000B0")
DEBUG_KEY_C = bytes.fromhex("010353484500800000000000000000B0")

EMPTY_KEY = b"\xff" * BLOCK       # a key slot never loaded ([RM13] 36.5.13.13 note 2, [AN5401] 4.2.1)
WILDCARD_UID = bytes(15)          # UID 0: allowed while the key is not wildcard protected ([AN5401] Appendix A)
MASTER_ECU_KEY_ID = 0x1           # SHE key slot 1 ([RM13] Table 36-75)
FIRST_COUNTER = 1                 # the counter of a key that is loaded for the first time ([AN5401] Appendix A)
COUNTER_MAX = (1 << 28) - 1


# ------------------------------------------------------------------------------------------------ AES-128 (FIPS-197)
def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x11B) if a & 0x100 else a


def _sbox() -> list[int]:
    # multiplicative inverse in GF(2^8) followed by the affine map (FIPS-197 5.1.1)
    inverse = [0] * 256
    for a in range(1, 256):
        for b in range(1, 256):
            p, x, y = 0, a, b
            while y:
                if y & 1:
                    p ^= x
                x, y = _xtime(x), y >> 1
            if p == 1:
                inverse[a] = b
                break
    box = []
    for a in range(256):
        b = inverse[a]
        s = b
        for shift in range(1, 5):
            s ^= ((b << shift) | (b >> (8 - shift))) & 0xFF
        box.append(s ^ 0x63)
    return box


SBOX = _sbox()


def _expand(key: bytes) -> list[list[int]]:
    if len(key) != BLOCK:
        raise ValueError("AES-128 key must be 16 bytes")
    words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(words[i - 1])
        if i % 4 == 0:
            t = [SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        words.append([words[i - 4][j] ^ t[j] for j in range(4)])
    return [sum(words[4 * r:4 * r + 4], []) for r in range(11)]


def aes128_encrypt(key: bytes, block: bytes) -> bytes:
    """One AES-128 block (FIPS-197 5.1)."""
    if len(block) != BLOCK:
        raise ValueError("AES block must be 16 bytes")
    keys = _expand(key)
    s = [b ^ k for b, k in zip(block, keys[0])]
    for rnd in range(1, 11):
        s = [SBOX[b] for b in s]
        # ShiftRows on the column-major state: byte r + 4c comes from column (c + r) mod 4
        s = [s[r + 4 * ((c + r) % 4)] for c in range(4) for r in range(4)]
        if rnd != 10:
            mixed = []
            for c in range(4):
                a = s[4 * c:4 * c + 4]
                t = a[0] ^ a[1] ^ a[2] ^ a[3]
                mixed += [a[r] ^ t ^ _xtime(a[r] ^ a[(r + 1) % 4]) for r in range(4)]
            s = mixed
        s = [b ^ k for b, k in zip(s, keys[rnd])]
    return bytes(s)


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-128-CBC without padding (NIST SP 800-38A 6.2)."""
    if len(data) % BLOCK:
        raise ValueError("CBC data must be a multiple of 16 bytes")
    out, chain = b"", iv
    for i in range(0, len(data), BLOCK):
        chain = aes128_encrypt(key, _xor(data[i:i + BLOCK], chain))
        out += chain
    return out


def _double(block: bytes) -> bytes:
    n = int.from_bytes(block, "big") << 1
    if n >> 128:
        n = (n ^ 0x87) & ((1 << 128) - 1)
    return n.to_bytes(BLOCK, "big")


def cmac(key: bytes, data: bytes) -> bytes:
    """AES-128-CMAC, full 128-bit tag (NIST SP 800-38B, RFC 4493)."""
    k1 = _double(aes128_encrypt(key, bytes(BLOCK)))
    k2 = _double(k1)
    if data and len(data) % BLOCK == 0:
        last = _xor(data[-BLOCK:], k1)
        head = data[:-BLOCK]
    else:
        rest = len(data) % BLOCK
        head = data[:len(data) - rest]
        last = _xor(data[len(data) - rest:] + b"\x80" + bytes(BLOCK - rest - 1), k2)
    chain = bytes(BLOCK)
    for i in range(0, len(head), BLOCK):
        chain = aes128_encrypt(key, _xor(head[i:i + BLOCK], chain))
    return aes128_encrypt(key, _xor(last, chain))


# ------------------------------------------------------------------------------------------------ SHE
def mp_compress(data: bytes) -> bytes:
    """Miyaguchi-Preneel compression of already padded data: H0 = 0, Hi = E_Hi-1(xi) ^ xi ^ Hi-1."""
    if not data or len(data) % BLOCK:
        raise ValueError("MP compression needs whole 16-byte blocks (padding is the caller's)")
    h = bytes(BLOCK)
    for i in range(0, len(data), BLOCK):
        x = data[i:i + BLOCK]
        h = _xor(_xor(aes128_encrypt(h, x), x), h)
    return h


def kdf(key: bytes, constant: bytes) -> bytes:
    """SHE key derivation KDF(K, C) = AES-MP(K | C)."""
    if len(key) != BLOCK or len(constant) != BLOCK:
        raise ValueError("KDF takes a 16-byte key and a 16-byte constant")
    return mp_compress(key + constant)


def _key(value: bytes, what: str) -> bytes:
    if not isinstance(value, (bytes, bytearray)) or len(value) != BLOCK:
        raise ValueError(f"{what} must be 16 bytes")
    return bytes(value)


def _uid(value: bytes) -> bytes:
    if not isinstance(value, (bytes, bytearray)) or len(value) != 15:
        raise ValueError("UID must be 15 bytes (120 bits)")
    return bytes(value)


def _ids(key_id: int, auth_id: int) -> int:
    for name, value in (("key id", key_id), ("auth id", auth_id)):
        if not 1 <= value <= 0xF:
            raise ValueError(f"{name} must be 1..15 (4 bits, KBS not included)")
    return (key_id << 4) | auth_id


@dataclass(frozen=True)
class KeyUpdate:
    """The messages of one CMD_LOAD_KEY: M1..M3 go to the device, M4/M5 are what it must answer."""
    m1: bytes
    m2: bytes
    m3: bytes
    m4: bytes
    m5: bytes


def memory_update(new_key: bytes, key_id: int, auth_id: int, auth_key: bytes, counter: int,
                  uid: bytes = WILDCARD_UID) -> KeyUpdate:
    """SHE memory update protocol ([AN5401] Appendix A). The key flags are always 0 (no WRITE_PROTECTION, no
    BOOT_PROTECTION, no DEBUGGER_PROTECTION, KEY_USAGE 0, no WILDCARD protection): there is no parameter for them."""
    new_key, auth_key, uid = _key(new_key, "new key"), _key(auth_key, "authorising key"), _uid(uid)
    if not 1 <= counter <= COUNTER_MAX:
        raise ValueError("counter must be 1..2^28-1")
    k1 = kdf(auth_key, KEY_UPDATE_ENC_C)
    k2 = kdf(auth_key, KEY_UPDATE_MAC_C)
    m1 = uid + bytes([_ids(key_id, auth_id)])
    m2 = cbc_encrypt(k1, bytes(BLOCK), counter_block(counter) + new_key)
    m3 = cmac(k2, m1 + m2)
    k3 = kdf(new_key, KEY_UPDATE_ENC_C)
    k4 = kdf(new_key, KEY_UPDATE_MAC_C)
    m4_star = aes128_encrypt(k3, ((counter << 100) | (1 << 99)).to_bytes(BLOCK, "big"))
    m4 = m1 + m4_star
    m5 = cmac(k4, m4)
    return KeyUpdate(m1, m2, m3, m4, m5)


def counter_block(counter: int) -> bytes:
    """First plaintext block of M2: CID (28 bits) | FID (5 bits, always 0 here) | 95 zero bits."""
    if not 1 <= counter <= COUNTER_MAX:
        raise ValueError("counter must be 1..2^28-1")
    return (counter << 100).to_bytes(BLOCK, "big")


def first_master_key_load(master_key: bytes, uid: bytes = WILDCARD_UID) -> KeyUpdate:
    """CMD_LOAD_KEY of the MASTER_ECU_KEY into its empty slot: authorised by itself, authorising value all ones,
    counter 1 ([RM13] 36.5.13.13 note 2, [AN5401] 4.2.1). The only load the recovery of NVM-13 ever does."""
    return memory_update(master_key, MASTER_ECU_KEY_ID, MASTER_ECU_KEY_ID, EMPTY_KEY, FIRST_COUNTER, uid)


def first_load_m2_block0() -> bytes:
    """First block of M2 of every first MASTER_ECU_KEY load (counter 1, no flags, authorising key all ones): with
    CBC and IV 0 it depends on nothing else. The device refuses any M2 that does not start with it, so it can never
    pass on a key flag - WRITE_PROTECTION in particular (bspCsec, CsecRecovery.cpp FIRST_LOAD_M2_BLOCK0)."""
    return aes128_encrypt(kdf(EMPTY_KEY, KEY_UPDATE_ENC_C), counter_block(FIRST_COUNTER))


def check_answer(update: KeyUpdate, m4: bytes, m5: bytes) -> list[str]:
    """Differences between the device's M4/M5 and the expected ones (empty: the key is loaded as intended)."""
    problems = []
    if bytes(m4) != update.m4:
        if len(m4) == 32 and bytes(m4[:15]) != update.m4[:15] and update.m1[:15] == WILDCARD_UID:
            problems.append("M4 carries the device UID (expected with a wildcard M1 - recompute with --uid)")
        else:
            problems.append(f"M4 {bytes(m4).hex().upper()} != expected {update.m4.hex().upper()}")
    if bytes(m5) != update.m5:
        problems.append(f"M5 {bytes(m5).hex().upper()} != expected {update.m5.hex().upper()}")
    return problems


def debug_key(master_key: bytes) -> bytes:
    """K = KDF(MASTER_ECU_KEY, DEBUG_KEY_C) ([RM13] Table 36-99, [AN5401] 4.5)."""
    return kdf(_key(master_key, "MASTER_ECU_KEY"), DEBUG_KEY_C)


def debug_authorization(master_key: bytes, challenge: bytes, uid: bytes) -> bytes:
    """AUTHORIZATION = CMAC_K(CHALLENGE | UID) for CMD_DBG_AUTH."""
    return cmac(debug_key(master_key), _key(challenge, "challenge") + _uid(uid))
