"""Host test of the C library libs/bsw/e2e against the Python reference model e2e_ref.py (COM-10).

Builds the library with Zig as DLL (C99, -Wall -Wextra -Wconversion -Werror) and compares it with the independently
written reference model on randomised, reproducible scenarios: random layouts, Data IDs and tolerances per profile
(own test profiles, no board values), and a channel that delivers, loses, repeats, corrupts, delays or swaps messages.
After every Protect and Check call the return code, the data bytes and the complete state must match.
Also builds and runs the C host test libs/bsw/e2e/test/host/E2eHostTest.c (specification examples).

Run: .venv/Scripts/python -m unittest tools/e2e/test_e2e_host.py
Environment: E2E_SOURCE=<repository copy> and E2E_WERROR=0 (used by test_e2e_mutations.py), E2E_SEED=<int> (default 10).
"""

import ctypes
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import e2e_ref as ref  # noqa: E402

SOURCES = ("E2E.c", "E2E_P01.c", "E2E_P02.c", "E2E_P11.c")
WARNINGS = ["-std=c99", "-Wall", "-Wextra", "-Wconversion"]


def _source_root() -> Path:
    return Path(os.environ.get("E2E_SOURCE", ROOT))


def _zig_cc(args, werror: bool) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "ziglang", "cc", "-O1", *WARNINGS, *(["-Werror"] if werror else []), *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def build_dll(out: Path, source: Path, werror: bool = True) -> None:
    lib = source / "libs" / "bsw" / "e2e"
    result = _zig_cc(["-target", "x86_64-windows-gnu", "-shared", f"-I{lib / 'include'}",
                      *[str(lib / "src" / "e2e" / s) for s in SOURCES], "-o", str(out)], werror)
    if result.returncode != 0:
        raise RuntimeError(f"building {out.name} failed:\n{result.stderr}")


def build_host_test(out: Path, source: Path, werror: bool = True) -> subprocess.CompletedProcess:
    lib = source / "libs" / "bsw" / "e2e"
    return _zig_cc([f"-I{lib / 'include'}", *[str(lib / "src" / "e2e" / s) for s in SOURCES],
                    str(lib / "test" / "host" / "E2eHostTest.c"), "-o", str(out)], werror)


# --- ctypes mirror of the public structures (plain uint8_t/uint16_t/bool members, no enums) -------------------------

u8, u16, cbool = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_bool


class P01Config(ctypes.Structure):
    _fields_ = [("CounterOffset", u16), ("CRCOffset", u16), ("DataID", u16), ("DataIDNibbleOffset", u16),
                ("DataIDMode", u8), ("DataLength", u16), ("MaxDeltaCounterInit", u8), ("MaxNoNewOrRepeatedData", u8),
                ("SyncCounterInit", u8)]


class LegacyCheckState(ctypes.Structure):
    _fields_ = [("LastValidCounter", u8), ("MaxDeltaCounter", u8), ("WaitForFirstData", cbool), ("LostData", u8),
                ("Status", u8), ("SyncCounter", u8), ("NoNewOrRepeatedDataCounter", u8)]


class P02Config(ctypes.Structure):
    _fields_ = [("DataLength", u16), ("DataIDList", u8 * 16), ("MaxDeltaCounterInit", u8),
                ("MaxNoNewOrRepeatedData", u8), ("SyncCounterInit", u8)]


class P11Config(ctypes.Structure):
    _fields_ = [("CounterOffset", u16), ("CRCOffset", u16), ("DataID", u16), ("DataIDNibbleOffset", u16),
                ("DataIDMode", u8), ("DataLength", u16), ("MaxDeltaCounter", u8)]


class P11CheckState(ctypes.Structure):
    _fields_ = [("Counter", u8), ("Status", u8)]


class ProtectState(ctypes.Structure):
    _fields_ = [("Counter", u8)]


_DLL = None


def load_dll() -> ctypes.CDLL:
    global _DLL
    if _DLL is None:
        path = Path(tempfile.gettempdir()) / f"e2e_host_{os.getpid()}.dll"
        build_dll(path, _source_root(), werror=os.environ.get("E2E_WERROR", "1") != "0")
        dll = ctypes.CDLL(str(path))
        buf = ctypes.POINTER(u8)
        for name, cfg, state in (("P01", P01Config, LegacyCheckState), ("P02", P02Config, LegacyCheckState),
                                 ("P11", P11Config, P11CheckState)):
            getattr(dll, f"E2E_{name}Protect").argtypes = [ctypes.POINTER(cfg), ctypes.POINTER(ProtectState), buf, u16]
            getattr(dll, f"E2E_{name}Check").argtypes = [ctypes.POINTER(cfg), ctypes.POINTER(state), buf, u16]
            getattr(dll, f"E2E_{name}CheckInit").argtypes = [ctypes.POINTER(state)]
            getattr(dll, f"E2E_{name}CheckConfig").argtypes = [ctypes.POINTER(cfg)]
            for fn in ("Protect", "Check", "CheckInit", "CheckConfig"):
                getattr(dll, f"E2E_{name}{fn}").restype = u8
        for fn in ("E2E_P01MapStatusToSM", "E2E_P02MapStatusToSM"):
            getattr(dll, fn).argtypes = [u8, u8, cbool]
            getattr(dll, fn).restype = u8
        dll.E2E_P11MapStatusToSM.argtypes = [u8, u8]
        dll.E2E_P11MapStatusToSM.restype = u8
        for fn in ("E2E_Crc8", "E2E_Crc8H2F"):
            getattr(dll, fn).argtypes = [buf, ctypes.c_uint32, u8, cbool]
            getattr(dll, fn).restype = u8
        _DLL = dll
    return _DLL


def _buffer(data):
    return (u8 * max(len(data), 1))(*data)


def _legacy_state(st: LegacyCheckState) -> tuple:
    return (st.LastValidCounter, st.MaxDeltaCounter, bool(st.WaitForFirstData), st.LostData, st.Status,
            st.SyncCounter, st.NoNewOrRepeatedDataCounter)


def _ref_legacy_state(st: ref.P01CheckState) -> tuple:
    return (st.last_valid_counter, st.max_delta_counter, st.wait_for_first_data, st.lost_data, st.status,
            st.sync_counter, st.no_new_or_repeated_data_counter)


# --- random test profiles ----------------------------------------------------------------------------------------

LEGACY_STATUSES = (ref.P01STATUS_OK, ref.P01STATUS_OKSOMELOST, ref.P01STATUS_WRONGCRC, ref.P01STATUS_SYNC,
                   ref.P01STATUS_INITIAL, ref.P01STATUS_REPEATED, ref.P01STATUS_NONEWDATA, ref.P01STATUS_WRONGSEQUENCE)


def random_layout(rng: random.Random, nibble: bool):
    """Random valid header position: CRC byte, counter nibble outside it, Data ID nibble outside both."""
    n = rng.choice([2, 3, 4, 5, 6, 7, 8, 8, 8, 16, 32])
    crc = rng.randrange(n) * 8
    nibbles = [o for o in range(0, n * 8, 4) if o // 8 != crc // 8]
    counter = rng.choice(nibbles)
    id_nibble = rng.choice([o for o in nibbles if o != counter]) if nibble else rng.choice([0, 12])
    return n, crc, counter, id_nibble


class Channel:
    """Sender -> receiver events. Yields (data or None) for each check call."""

    EVENTS = ("deliver",) * 10 + ("lose", "lose2", "repeat", "corrupt", "nonew", "stale", "idbyte", "burstloss")

    def __init__(self, rng: random.Random):
        self.rng = rng

    def events(self, steps: int):
        return [self.rng.choice(self.EVENTS) for _ in range(steps)]


class CrcCompareTest(unittest.TestCase):
    def test_crc_random_and_chained(self):
        dll, rng = load_dll(), random.Random(1)
        for _ in range(2000):
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
            self.assertEqual(dll.E2E_Crc8(_buffer(data), len(data), 0, True), ref.crc8_sae_j1850(data))
            self.assertEqual(dll.E2E_Crc8H2F(_buffer(data), len(data), 0, True), ref.crc8_h2f(data))
            cut = rng.randrange(len(data) + 1)
            first = dll.E2E_Crc8(_buffer(data[:cut]), cut, 0, True)
            self.assertEqual(dll.E2E_Crc8(_buffer(data[cut:]), len(data) - cut, first, False),
                             ref.crc8_sae_j1850(data))


class ConfigCompareTest(unittest.TestCase):
    def test_config_validation_random(self):
        """Valid random layouts, half of them with one field disturbed; also Protect with a wrong buffer length."""
        dll, rng = load_dll(), random.Random(2)
        for _ in range(6000):
            mode = rng.randrange(0, 4)
            n, crc, counter, nib = random_layout(rng, True)
            vals = dict(counter_offset=counter, crc_offset=crc, data_id=rng.randrange(0x1000),
                        data_id_nibble_offset=nib, data_id_mode=mode, data_length=n * 8)
            if rng.random() < 0.5:
                key = rng.choice(list(vals))
                vals[key] = rng.choice([rng.randrange(0x10000), vals[key] + rng.choice([-8, -4, -1, 1, 4, 8]),
                                        0xF000 | vals[key], 5, 256, 264])
                vals[key] = max(0, min(vals[key], 0xFFFF))
                if key == "data_id_mode":
                    vals[key] &= 0xFF
            c01 = P01Config(vals["counter_offset"], vals["crc_offset"], vals["data_id"], vals["data_id_nibble_offset"],
                            vals["data_id_mode"], vals["data_length"], 1, 1, 0)
            c11 = P11Config(vals["counter_offset"], vals["crc_offset"], vals["data_id"], vals["data_id_nibble_offset"],
                            vals["data_id_mode"], vals["data_length"], 1)
            self.assertEqual(dll.E2E_P11CheckConfig(ctypes.byref(c11)) == ref.E_OK,
                             ref._p11_config_ok(ref.P11Config(**vals, max_delta_counter=1)), vals)
            self.assertEqual(dll.E2E_P01CheckConfig(ctypes.byref(c01)) == ref.E_OK,
                             ref._p01_config_ok(ref.P01Config(**vals)), vals)
            length = max(1, vals["data_length"] // 8 + rng.choice([0, 0, -1, 1]))
            buf = [0] * length
            self.assertEqual(dll.E2E_P11Protect(ctypes.byref(c11), ctypes.byref(ProtectState(0)), _buffer(buf), length),
                             ref.p11_protect(ref.P11Config(**vals, max_delta_counter=1), 0, bytearray(buf))[0], vals)
            self.assertEqual(dll.E2E_P01Protect(ctypes.byref(c01), ctypes.byref(ProtectState(0)), _buffer(buf), length),
                             ref.p01_protect(ref.P01Config(**vals), 0, bytearray(buf))[0], vals)


class ScenarioCompareTest(unittest.TestCase):
    SCENARIOS = 250
    STEPS = 80

    def setUp(self):
        self.dll = load_dll()
        self.rng = random.Random(int(os.environ.get("E2E_SEED", "10")))

    def _run(self, name, make, protect_c, protect_ref, check_c, check_ref, compare_state, map_c, map_ref, other_id):
        """Returns the set of (E_OK, status) and error return codes that occurred."""
        seen = set()
        for scenario in range(self.SCENARIOS):
            cfg_c, cfg_ref, cfg_other_c, n = make()
            tx_c, tx_ref = ProtectState(self.rng.randrange(15)), None
            tx_ref = tx_c.Counter
            st_c, st_ref = check_c.init()
            history = []
            ctx = f"{name} scenario {scenario}: {cfg_ref}"

            def send():
                nonlocal tx_ref
                payload = [self.rng.randrange(256) for _ in range(n)]
                buf_c, buf_ref = _buffer(payload), bytearray(payload)
                rc_c = protect_c(ctypes.byref(cfg_c), ctypes.byref(tx_c), buf_c, n)
                rc_ref, tx_ref = protect_ref(cfg_ref, tx_ref, buf_ref)
                self.assertEqual((rc_c, tx_c.Counter, bytes(buf_c[:n])), (rc_ref, tx_ref, bytes(buf_ref)), ctx)
                history.append(bytes(buf_ref))
                return bytes(buf_ref)

            for step, event in enumerate(Channel(self.rng).events(self.STEPS)):
                cfg_used_c, data = cfg_c, None
                if not history:
                    event = "deliver"
                if event == "deliver":
                    data = send()
                elif event == "lose":
                    send()
                    data = send()
                elif event == "lose2":
                    for _ in range(2):
                        send()
                    data = send()
                elif event == "burstloss":
                    for _ in range(self.rng.randrange(3, 20)):
                        send()
                    data = send()
                elif event == "repeat":
                    data = history[-1]
                elif event == "stale":
                    data = self.rng.choice(history)
                elif event == "corrupt":
                    raw = bytearray(send())
                    bit = self.rng.randrange(n * 8)
                    raw[bit // 8] ^= 1 << (bit % 8)
                    data = bytes(raw)
                elif event == "idbyte":
                    data = send()
                    cfg_used_c = cfg_other_c
                if event == "idbyte":
                    rc_c = check_c.call(ctypes.byref(cfg_used_c), ctypes.byref(st_c), _buffer(data), n)
                    rc_ref = check_ref(other_id(cfg_ref), st_ref, data)
                elif event == "nonew":
                    rc_c = check_c.call(ctypes.byref(cfg_c), ctypes.byref(st_c), None, 0)
                    rc_ref = check_ref(cfg_ref, st_ref, None)
                else:
                    rc_c = check_c.call(ctypes.byref(cfg_c), ctypes.byref(st_c), _buffer(data), n)
                    rc_ref = check_ref(cfg_ref, st_ref, data)
                where = f"{ctx} step {step} {event} data={data.hex() if data else None}"
                self.assertEqual(rc_c, rc_ref, where)
                compare_state(st_c, st_ref, where)
                self.assertEqual(map_c(rc_c, st_c), map_ref(rc_ref, st_ref), where)
                seen.add((rc_ref, st_c.Status) if rc_ref == ref.E_OK else rc_ref)
        return seen

    def test_p01(self):
        rng = self.rng

        def make():
            mode = rng.randrange(4)
            n, crc, counter, nib = random_layout(rng, mode == ref.DATAID_NIBBLE)
            data_id = rng.randrange(0x1000) if mode == ref.DATAID_NIBBLE else rng.randrange(0x10000)
            tol = (rng.randrange(0, 15), rng.randrange(0, 16), rng.choice([0, 0, 1, 2, 3]))
            cfg_ref = ref.P01Config(counter, crc, data_id, nib, mode, n * 8, *tol)
            other = P01Config(counter, crc, data_id ^ rng.choice([0x0001, 0x0100, 0x0800]), nib, mode, n * 8, *tol)
            return P01Config(counter, crc, data_id, nib, mode, n * 8, *tol), cfg_ref, other, n

        class Check:
            @staticmethod
            def init():
                st = LegacyCheckState()
                self.assertEqual(self.dll.E2E_P01CheckInit(ctypes.byref(st)), 0)
                return st, ref.P01CheckState()
            call = self.dll.E2E_P01Check

        seen = self._run_with_other("P01", make, self.dll.E2E_P01Protect, ref.p01_protect, Check, ref.p01_check,
                                    lambda c, r, w: self.assertEqual(_legacy_state(c), _ref_legacy_state(r), w),
                                    lambda rc, st: self.dll.E2E_P01MapStatusToSM(rc, st.Status, True),
                                    lambda rc, st: ref.legacy_map_status(rc, st.status, True),
                                    lambda c: ref.P01Config(c.CounterOffset, c.CRCOffset, c.DataID,
                                                            c.DataIDNibbleOffset, c.DataIDMode, c.DataLength,
                                                            c.MaxDeltaCounterInit, c.MaxNoNewOrRepeatedData,
                                                            c.SyncCounterInit))
        self.assertCovered(seen, LEGACY_STATUSES, (ref.E_INPUTERR_WRONG,))

    def test_p02(self):
        rng = self.rng

        def make():
            n = rng.choice([2, 3, 4, 8, 8, 8, 16, 64])
            ids = [rng.randrange(256) for _ in range(16)]
            tol = (rng.randrange(0, 16), rng.randrange(0, 16), rng.choice([0, 0, 1, 2, 3]))
            other_ids = list(ids)
            other_ids[rng.randrange(16)] ^= 0x01
            cfg_c = P02Config(n * 8, (u8 * 16)(*ids), *tol)
            other = P02Config(n * 8, (u8 * 16)(*other_ids), *tol)
            return cfg_c, ref.P02Config(n * 8, ids, *tol), other, n

        class Check:
            @staticmethod
            def init():
                st = LegacyCheckState()
                self.assertEqual(self.dll.E2E_P02CheckInit(ctypes.byref(st)), 0)
                return st, ref.P02CheckState()
            call = self.dll.E2E_P02Check

        seen = self._run_with_other("P02", make, self.dll.E2E_P02Protect, ref.p02_protect, Check, ref.p02_check,
                                    lambda c, r, w: self.assertEqual(_legacy_state(c), _ref_legacy_state(r), w),
                                    lambda rc, st: self.dll.E2E_P02MapStatusToSM(rc, st.Status, False),
                                    lambda rc, st: ref.legacy_map_status(rc, st.status, False),
                                    lambda c: ref.P02Config(c.DataLength, list(c.DataIDList), c.MaxDeltaCounterInit,
                                                            c.MaxNoNewOrRepeatedData, c.SyncCounterInit))
        self.assertCovered(seen, LEGACY_STATUSES)

    def test_p11(self):
        rng = self.rng

        def make():
            mode = rng.choice([ref.DATAID_BOTH, ref.DATAID_NIBBLE])
            n, crc, counter, nib = random_layout(rng, mode == ref.DATAID_NIBBLE)
            data_id = rng.randrange(0x1000) if mode == ref.DATAID_NIBBLE else rng.randrange(0x10000)
            max_delta = rng.randrange(0, 15)
            other = P11Config(counter, crc, data_id ^ rng.choice([0x0001, 0x0100, 0x0400]), nib, mode, n * 8,
                              max_delta)
            return (P11Config(counter, crc, data_id, nib, mode, n * 8, max_delta),
                    ref.P11Config(counter, crc, data_id, nib, mode, n * 8, max_delta), other, n)

        class Check:
            @staticmethod
            def init():
                st = P11CheckState()
                self.assertEqual(self.dll.E2E_P11CheckInit(ctypes.byref(st)), 0)
                return st, ref.P11CheckState()
            call = self.dll.E2E_P11Check

        seen = self._run_with_other("P11", make, self.dll.E2E_P11Protect, ref.p11_protect, Check, ref.p11_check,
                                    lambda c, r, w: self.assertEqual((c.Counter, c.Status), (r.counter, r.status), w),
                                    lambda rc, st: self.dll.E2E_P11MapStatusToSM(rc, st.Status),
                                    lambda rc, st: ref.p11_map_status(rc, st.status),
                                    lambda c: ref.P11Config(c.CounterOffset, c.CRCOffset, c.DataID,
                                                            c.DataIDNibbleOffset, c.DataIDMode, c.DataLength,
                                                            c.MaxDeltaCounter))
        self.assertCovered(seen, (ref.P11STATUS_OK, ref.P11STATUS_OKSOMELOST, ref.P11STATUS_ERROR,
                                  ref.P11STATUS_REPEATED, ref.P11STATUS_NONEWDATA, ref.P11STATUS_WRONGSEQUENCE))

    def _run_with_other(self, name, make, protect_c, protect_ref, check, check_ref, compare, map_c, map_ref, to_ref):
        """Wraps make() so that the "idbyte" event checks with the other configuration in both implementations."""
        current = {}

        def make_wrapped():
            cfg_c, cfg_ref, other_c, n = make()
            current["other"] = to_ref(other_c)
            return cfg_c, cfg_ref, other_c, n

        return self._run(name, make_wrapped, protect_c, protect_ref, check, check_ref, compare, map_c, map_ref,
                         lambda _cfg: current["other"])

    def assertCovered(self, seen, statuses, errors=()):
        missing = [hex(s) for s in statuses if (ref.E_OK, s) not in seen] + [hex(e) for e in errors if e not in seen]
        self.assertFalse(missing, f"scenarios never produced {missing}")


class CHostTest(unittest.TestCase):
    def test_c_host_test_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "e2e_test.exe"
            build = build_host_test(exe, _source_root())
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n  " + run.stdout.strip().splitlines()[-1], end="")
            self.assertEqual(run.returncode, 0, run.stdout[-3000:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
