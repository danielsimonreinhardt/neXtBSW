"""Reference model of the AUTOSAR E2E profiles 1, 2 and 11 (COM-10).

Written directly from the AUTOSAR specifications, independently of the C library libs/bsw/e2e:
  - AUTOSAR_PRS_E2EProtocol (FO R22-11): 6.5 Profile 1, 6.6 Profile 2, 6.12 Profile 11 (flow charts, examples)
  - AUTOSAR_SWS_E2ELibrary (CP R22-11): 8.2 types and enum values, 8.3 init values and MapStatusToSM tables
  - AUTOSAR_SWS_CRCLibrary (CP R22-11): 7.2.1 CRC8 SAE J1850 and CRC8H2F parameters

Implementation style deliberately differs from the C code: CRCs are table driven and computed over the complete
byte sequence (DataID bytes + data without the CRC byte) with the effective start/XOR values the specification
states, instead of chaining Crc_CalculateCRC8 calls. Only the standard library is used, so tools/canflash and other
PC tests can import it (sys.path.insert(0, "tools/e2e"); import e2e_ref).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

# --- common types (SWS_E2E_00047, SWS_E2E_00347) ---------------------------------------------------------------------

E_OK = 0x00
E_INPUTERR_NULL = 0x13
E_INPUTERR_WRONG = 0x17

P_OK = 0x00
P_REPEATED = 0x01
P_WRONGSEQUENCE = 0x02
P_ERROR = 0x03
P_NOTAVAILABLE = 0x04
P_NONEWDATA = 0x05


# --- CRC (SWS_Crc_00030, SWS_Crc_00042) ------------------------------------------------------------------------------

def _table(poly: int) -> List[int]:
    out = []
    for value in range(256):
        reg = value
        for _ in range(8):
            reg = ((reg << 1) ^ poly) & 0xFF if reg & 0x80 else (reg << 1) & 0xFF
        out.append(reg)
    return out


_T_1D = _table(0x1D)
_T_2F = _table(0x2F)


def crc8_generic(data: Sequence[int], table: Sequence[int], init: int, xorout: int) -> int:
    reg = init
    for byte in data:
        reg = table[reg ^ (byte & 0xFF)]
    return reg ^ xorout


def crc8_sae_j1850(data: Sequence[int]) -> int:
    """CRC library Crc_CalculateCRC8 single call: polynomial 0x1D, init 0xFF, XOR 0xFF."""
    return crc8_generic(data, _T_1D, 0xFF, 0xFF)


def crc8_h2f(data: Sequence[int]) -> int:
    """CRC library Crc_CalculateCRC8H2F single call: polynomial 0x2F, init 0xFF, XOR 0xFF."""
    return crc8_generic(data, _T_2F, 0xFF, 0xFF)


# --- nibble access, bit offsets as in the configuration types (byte = offset/8, low nibble if offset%8 == 0) ---------

def _get_nibble(data: Sequence[int], bit_offset: int) -> int:
    byte = data[bit_offset // 8]
    return (byte >> 4) & 0x0F if bit_offset % 8 else byte & 0x0F


def _set_nibble(data: bytearray, bit_offset: int, value: int) -> None:
    i = bit_offset // 8
    if bit_offset % 8:
        data[i] = (data[i] & 0x0F) | ((value & 0x0F) << 4)
    else:
        data[i] = (data[i] & 0xF0) | (value & 0x0F)


# --- Profile 1 and 11: CRC over DataID and data ----------------------------------------------------------------------

DATAID_BOTH = 0
DATAID_ALT = 1
DATAID_LOW = 2
DATAID_NIBBLE = 3


def p01_crc(data: Sequence[int], length_bytes: int, crc_offset: int, data_id: int, mode: int, counter: int) -> int:
    """PRS_E2E_00070/00082/00163/00640: CRC-8 polynomial 0x1D with start value and XOR value 0x00, first over the Data
    ID byte(s) (low byte first), then over all data bytes except the CRC byte."""
    low, high = data_id & 0xFF, (data_id >> 8) & 0xFF
    if mode == DATAID_BOTH:
        prefix = [low, high]
    elif mode == DATAID_LOW:
        prefix = [low]
    elif mode == DATAID_ALT:
        prefix = [low] if counter % 2 == 0 else [high]
    elif mode == DATAID_NIBBLE:
        prefix = [low, 0x00]
    else:
        raise ValueError(mode)
    crc_byte = crc_offset // 8
    payload = [b for i, b in enumerate(data[:length_bytes]) if i != crc_byte]
    return crc8_generic(prefix + payload, _T_1D, 0x00, 0x00)


# --- Profile 1 -------------------------------------------------------------------------------------------------------

P01STATUS_OK = 0x00
P01STATUS_NONEWDATA = 0x01
P01STATUS_WRONGCRC = 0x02
P01STATUS_SYNC = 0x03
P01STATUS_INITIAL = 0x04
P01STATUS_REPEATED = 0x08
P01STATUS_OKSOMELOST = 0x20
P01STATUS_WRONGSEQUENCE = 0x40


@dataclass
class P01Config:
    counter_offset: int = 8
    crc_offset: int = 0
    data_id: int = 0x123
    data_id_nibble_offset: int = 12
    data_id_mode: int = DATAID_BOTH
    data_length: int = 64
    max_delta_counter_init: int = 1
    max_no_new_or_repeated_data: int = 15
    sync_counter_init: int = 0


@dataclass
class P01CheckState:
    """E2E_P01CheckInit (SWS_E2E_00389)."""
    last_valid_counter: int = 0
    max_delta_counter: int = 0
    wait_for_first_data: bool = True
    lost_data: int = 0
    status: int = P01STATUS_NONEWDATA
    no_new_or_repeated_data_counter: int = 0
    sync_counter: int = 0


def _p01_config_ok(cfg: P01Config) -> bool:
    n = cfg.data_length // 8
    if cfg.data_length % 8 or not 16 <= cfg.data_length <= 256:
        return False
    if cfg.crc_offset % 8 or cfg.crc_offset // 8 >= n or cfg.counter_offset % 4 or cfg.counter_offset // 8 >= n:
        return False
    if cfg.counter_offset // 8 == cfg.crc_offset // 8:
        return False
    if cfg.data_id_mode not in (DATAID_BOTH, DATAID_ALT, DATAID_LOW, DATAID_NIBBLE):
        return False
    if cfg.data_id_mode == DATAID_NIBBLE:
        if (cfg.data_id & 0xF000 or cfg.data_id_nibble_offset % 4 or cfg.data_id_nibble_offset // 8 >= n
                or cfg.data_id_nibble_offset // 8 == cfg.crc_offset // 8
                or cfg.data_id_nibble_offset == cfg.counter_offset):
            return False
    return True


def p01_protect(cfg: P01Config, counter: int, data: bytearray) -> tuple:
    """E2E_P01Protect (PRS_E2E_00195). Returns (return code, next counter)."""
    if data is None:
        return E_INPUTERR_NULL, counter
    if not _p01_config_ok(cfg) or len(data) != cfg.data_length // 8 or not 0 <= counter <= 14:
        return E_INPUTERR_WRONG, counter
    _set_nibble(data, cfg.counter_offset, counter)
    if cfg.data_id_mode == DATAID_NIBBLE:
        _set_nibble(data, cfg.data_id_nibble_offset, cfg.data_id >> 8)
    data[cfg.crc_offset // 8] = p01_crc(data, len(data), cfg.crc_offset, cfg.data_id, cfg.data_id_mode, counter)
    return E_OK, (counter + 1) % 15


def _legacy_process_counter(st, rx: int, modulo: int, cfg) -> int:
    """Figures 6.60-6.63 (P01) and 6.72-6.76 / PRS_E2E_00145..00151, 00298..00301 (P02)."""
    cap = modulo - 1
    delta = rx - st.last_valid_counter
    if delta < 0:
        delta += modulo
    if delta == 0:
        st.no_new_or_repeated_data_counter = min(st.no_new_or_repeated_data_counter + 1, cap)
        return P01STATUS_REPEATED
    if delta > st.max_delta_counter:
        st.no_new_or_repeated_data_counter = 0
        st.sync_counter = cfg.sync_counter_init
        if st.sync_counter > 0:
            st.max_delta_counter = cfg.max_delta_counter_init
            st.last_valid_counter = rx
        return P01STATUS_WRONGSEQUENCE
    st.max_delta_counter = cfg.max_delta_counter_init
    st.last_valid_counter = rx
    st.lost_data = delta - 1
    if st.no_new_or_repeated_data_counter <= cfg.max_no_new_or_repeated_data:
        st.no_new_or_repeated_data_counter = 0
        if st.sync_counter > 0:
            st.sync_counter -= 1
            return P01STATUS_SYNC
        return P01STATUS_OK if delta == 1 else P01STATUS_OKSOMELOST
    st.sync_counter = cfg.sync_counter_init
    st.no_new_or_repeated_data_counter = 0
    return P01STATUS_SYNC


def p01_check(cfg: P01Config, st: P01CheckState, data: Optional[Sequence[int]]) -> int:
    """E2E_P01Check (PRS_E2E_00196, Figure 6.58). data None = no new data. Returns the return code, status in st."""
    if not _p01_config_ok(cfg) or (data is not None and len(data) != cfg.data_length // 8):
        return E_INPUTERR_WRONG
    st.max_delta_counter = min(st.max_delta_counter + 1, 14)
    if data is None:
        st.no_new_or_repeated_data_counter = min(st.no_new_or_repeated_data_counter + 1, 14)
        st.status = P01STATUS_NONEWDATA
        return E_OK
    rx = _get_nibble(data, cfg.counter_offset)
    if rx >= 15:
        return E_INPUTERR_WRONG
    crc_ok = data[cfg.crc_offset // 8] == p01_crc(data, len(data), cfg.crc_offset, cfg.data_id, cfg.data_id_mode, rx)
    if cfg.data_id_mode == DATAID_NIBBLE and _get_nibble(data, cfg.data_id_nibble_offset) != (cfg.data_id >> 8) & 0x0F:
        crc_ok = False
    if not crc_ok:
        st.status = P01STATUS_WRONGCRC
        return E_OK
    if st.wait_for_first_data:
        st.wait_for_first_data = False
        st.max_delta_counter = cfg.max_delta_counter_init
        st.last_valid_counter = rx
        st.status = P01STATUS_INITIAL
        return E_OK
    st.status = _legacy_process_counter(st, rx, 15, cfg)
    return E_OK


def legacy_map_status(check_return: int, status: int, profile_behavior: bool) -> int:
    """E2E_P01MapStatusToSM / E2E_P02MapStatusToSM (SWS_E2E_00383/00476/00384, Tables 8.1-8.4)."""
    if check_return != E_OK:
        return P_ERROR
    if status in (P01STATUS_OK, P01STATUS_OKSOMELOST):
        return P_OK
    if status == P01STATUS_WRONGCRC:
        return P_ERROR
    if status == P01STATUS_REPEATED:
        return P_REPEATED
    if status == P01STATUS_NONEWDATA:
        return P_NONEWDATA
    if status == P01STATUS_WRONGSEQUENCE:
        return P_WRONGSEQUENCE
    if status == P01STATUS_SYNC:
        return P_OK if profile_behavior else P_WRONGSEQUENCE
    if status == P01STATUS_INITIAL:
        return P_WRONGSEQUENCE if profile_behavior else P_OK
    return P_ERROR


# --- Profile 2 -------------------------------------------------------------------------------------------------------

@dataclass
class P02Config:
    data_length: int = 64
    data_id_list: List[int] = field(default_factory=lambda: list(range(1, 17)))
    max_delta_counter_init: int = 1
    max_no_new_or_repeated_data: int = 15
    sync_counter_init: int = 0


P02CheckState = P01CheckState  # same members and init values (SWS_E2E_00392); status values are identical


def _p02_config_ok(cfg: P02Config) -> bool:
    return cfg.data_length % 8 == 0 and 16 <= cfg.data_length <= 256 * 8 and len(cfg.data_id_list) == 16


def p02_crc(data: Sequence[int], data_id: int) -> int:
    """PRS_E2E_00132: CRC8H2F (start 0xFF, XOR 0xFF) over Data[1..DataLength/8-1] extended with the Data ID."""
    return crc8_h2f(list(data[1:]) + [data_id & 0xFF])


def p02_protect(cfg: P02Config, counter: int, data: bytearray) -> tuple:
    """E2E_P02Protect (Figures 6.65, 6.66): increment first, then write counter and CRC. Returns (rc, new counter)."""
    if data is None:
        return E_INPUTERR_NULL, counter
    if not _p02_config_ok(cfg) or len(data) != cfg.data_length // 8:
        return E_INPUTERR_WRONG, counter
    counter = 0 if counter >= 15 else counter + 1
    data[1] = (data[1] & 0xF0) | counter
    data[0] = p02_crc(data, cfg.data_id_list[counter])
    return E_OK, counter


def p02_check(cfg: P02Config, st: P02CheckState, data: Optional[Sequence[int]]) -> int:
    """E2E_P02Check (Figures 6.69-6.76)."""
    if not _p02_config_ok(cfg) or (data is not None and len(data) != cfg.data_length // 8):
        return E_INPUTERR_WRONG
    st.max_delta_counter = min(st.max_delta_counter + 1, 15)
    if data is None:
        st.no_new_or_repeated_data_counter = min(st.no_new_or_repeated_data_counter + 1, 15)
        st.status = P01STATUS_NONEWDATA
        return E_OK
    rx = data[1] & 0x0F
    if data[0] != p02_crc(data, cfg.data_id_list[rx]):
        st.status = P01STATUS_WRONGCRC
        return E_OK
    if st.wait_for_first_data:
        st.wait_for_first_data = False
        st.max_delta_counter = cfg.max_delta_counter_init
        st.last_valid_counter = rx
        st.status = P01STATUS_INITIAL
        return E_OK
    st.status = _legacy_process_counter(st, rx, 16, cfg)
    return E_OK


# --- Profile 11 ------------------------------------------------------------------------------------------------------

P11STATUS_OK = 0x00
P11STATUS_NONEWDATA = 0x01
P11STATUS_ERROR = 0x07
P11STATUS_REPEATED = 0x08
P11STATUS_OKSOMELOST = 0x20
P11STATUS_WRONGSEQUENCE = 0x40


@dataclass
class P11Config:
    counter_offset: int = 8
    crc_offset: int = 0
    data_id: int = 0x123
    data_id_nibble_offset: int = 12
    data_id_mode: int = DATAID_BOTH
    data_length: int = 64
    max_delta_counter: int = 1


@dataclass
class P11CheckState:
    """E2E_P11CheckInit (SWS_E2E_00556)."""
    counter: int = 0x0E
    status: int = P11STATUS_ERROR


def _p11_config_ok(cfg: P11Config) -> bool:
    if cfg.data_id_mode not in (DATAID_BOTH, DATAID_NIBBLE):
        return False
    as_p01 = P01Config(cfg.counter_offset, cfg.crc_offset, cfg.data_id, cfg.data_id_nibble_offset, cfg.data_id_mode,
                       cfg.data_length)
    return _p01_config_ok(as_p01)


def p11_protect(cfg: P11Config, counter: int, data: bytearray) -> tuple:
    """E2E_P11Protect (Figures 6.94-6.100)."""
    if data is None:
        return E_INPUTERR_NULL, counter
    if not _p11_config_ok(cfg) or len(data) != cfg.data_length // 8 or not 0 <= counter <= 14:
        return E_INPUTERR_WRONG, counter
    if cfg.data_id_mode == DATAID_NIBBLE:
        _set_nibble(data, cfg.data_id_nibble_offset, cfg.data_id >> 8)
    _set_nibble(data, cfg.counter_offset, counter)
    data[cfg.crc_offset // 8] = p01_crc(data, len(data), cfg.crc_offset, cfg.data_id, cfg.data_id_mode, counter)
    return E_OK, (counter + 1) % 15


def p11_check(cfg: P11Config, st: P11CheckState, data: Optional[Sequence[int]]) -> int:
    """E2E_P11Check (Figures 6.106-6.111). data None = no new data (Data NULL and Length 0)."""
    if not _p11_config_ok(cfg) or (data is not None and len(data) != cfg.data_length // 8):
        return E_INPUTERR_WRONG
    if data is None:
        st.status = P11STATUS_NONEWDATA
        return E_OK
    rx = _get_nibble(data, cfg.counter_offset)
    ok = data[cfg.crc_offset // 8] == p01_crc(data, len(data), cfg.crc_offset, cfg.data_id, cfg.data_id_mode, rx)
    if cfg.data_id_mode == DATAID_NIBBLE and _get_nibble(data, cfg.data_id_nibble_offset) != (cfg.data_id >> 8) & 0x0F:
        ok = False
    if not ok:
        st.status = P11STATUS_ERROR
        return E_OK
    delta = rx - st.counter if rx >= st.counter else 15 + rx - st.counter
    if 0 <= delta <= cfg.max_delta_counter:
        if delta == 0:
            st.status = P11STATUS_REPEATED
        else:
            st.status = P11STATUS_OK if delta == 1 else P11STATUS_OKSOMELOST
    else:
        st.status = P11STATUS_WRONGSEQUENCE
    st.counter = rx
    return E_OK


def p11_map_status(check_return: int, status: int) -> int:
    """E2E_P11MapStatusToSM (SWS_E2E_00557/00558, Table 8.13)."""
    if check_return != E_OK:
        return P_ERROR
    return {P11STATUS_OK: P_OK, P11STATUS_OKSOMELOST: P_OK, P11STATUS_ERROR: P_ERROR, P11STATUS_REPEATED: P_REPEATED,
            P11STATUS_NONEWDATA: P_NONEWDATA, P11STATUS_WRONGSEQUENCE: P_WRONGSEQUENCE}.get(status, P_ERROR)
