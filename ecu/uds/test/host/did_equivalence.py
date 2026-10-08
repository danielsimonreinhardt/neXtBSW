"""Before/after proof of COM-28: the data identifiers the application served by hand answer the same bytes when
they come from the diagnostic description (projects/<project>/diag.yaml, tools/diaggen).

`EXCHANGES` is one fixed sequence of requests on a freshly powered ECU. Each request goes through udsoncan (the
real ISO-TP client, multi-frame answers included) and the complete response payload is kept - positive or
negative. The file did_responses_before_com28.json holds what the hand written AppDataIdentifiers answered
(recorded with `record` below on the commit before COM-28, dev 19df315); test_app_uds_host.py runs the same
sequence against the generated jobs and compares byte by byte.

Three kinds of rows:
  same     the answer must be identical
  changed  the answer differs on purpose; `why` says which rule of the description or of ISO 14229-1 makes it
           differ, and `after` is the expected new answer
  setup    only sent to the generated stack (service 27 did not exist before COM-28); not compared

    .venv/Scripts/python ecu/uds/test/host/did_equivalence.py record   # only on the commit before COM-28
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "did_responses_before_com28.json"
# BL-10: the tests keep no copy of the bootloader version - F180 answers the one of bl_config.h
BL_VERSION_NUMBER = re.search(r'#define\s+BL_VERSION_NUMBER\s+"([^"]+)"',
                              (HERE.parents[3] / "bootloader" / "include" / "bl_config.h").read_text()).group(1)
BL_VERSION_HEX = " ".join(f"{c:02X}" for c in f"nextbsw-bl {BL_VERSION_NUMBER}".encode("ascii"))

# (kind, request payload, why/after). The session is the one the previous rows left.
EXCHANGES = [
    # boot and application software identification (numberOfModules + text)
    ("changed", "22 F1 80",
     ("COM-33: the text fills its length_bytes 32 of diag.yaml (NUL behind it), so that a tester can split a "
      "response with several identifiers by the description; before, it kept its runtime length",
      "62 F1 80 01 " + BL_VERSION_HEX + " 00" * (32 - len(f"nextbsw-bl {BL_VERSION_NUMBER}")))),
    ("changed", "22 F1 81",
     ("COM-33: as F180, the application text is filled up to 32 bytes",
      "62 F1 81 01 6E 65 78 74 62 73 77 2D 61 70 70 20 68 6F 73 74" + " 00" * 16)),
    ("same", "22 F1 86", None),                     # active session: default
    ("same", "22 FD 00", None),                     # RAM test value, readable in every session
    ("same", "22 12 34", None),                     # unknown identifier: requestOutOfRange
    ("same", "22 F1", None),                        # identifier cut off: incorrectMessageLength
    ("same", "22 F1 80 00", None),                  # odd length (1 + 2n violated)
    ("changed", "22 F1 86 FD 00",
     ("ISO 14229-1 10.2: several identifiers in one request are answered one after the other; the OpenBSW "
      "service before COM-28 accepted exactly one (NRC 0x13)", "62 F1 86 01 FD 00 00 00 00 00")),
    ("changed", "22 F1 86 12 34",
     ("10.2: an unsupported identifier among supported ones is left out, not refused",
      "62 F1 86 01")),
    ("same", "2E FD 00 01 02 03 04", None),         # write in the default session: not supported in this session
    ("same", "2E F1 86 03", None),                  # read-only identifier: requestOutOfRange
    ("same", "10 03", None),                        # extended session
    ("same", "22 F1 86", None),                     # active session: extended
    ("changed", "2E FD 00 01 02 03 04",
     ("diag.yaml RamTestValue: write_security Application - without an unlocked level the write is refused "
      "(10.7, NRC 0x33); before COM-28 nobody checked a security level", "7F 2E 33")),
    ("setup", "27 01", None),                       # requestSeed of the Application level
    ("setup", "27 02 KEY", None),                   # sendKey (key of the test algorithm of the host build)
    ("same", "2E FD 00 01 02 03 04", None),         # now accepted
    ("same", "22 FD 00", None),                     # and read back
    ("same", "2E FD 00 05", None),                  # wrong length
    ("same", "2E FD 00 01 02 03 04 05", None),      # wrong length, too long
    ("same", "22 FD 00", None),                     # unchanged by the refused writes
    ("same", "2E F1 80 01", None),                  # identification identifiers are read only
    ("same", "10 01", None),                        # back to the default session
    ("same", "22 FD 00", None),                     # the RAM value survives the session change
    ("same", "22 F1 86", None),
]


def payload(text: str) -> bytes:
    return bytes.fromhex(text)


def run(client, key_of=None) -> list[str | None]:
    """Sends EXCHANGES with a udsoncan client, returns the response payloads as hex (None: row not sent).

    `key_of(seed)`: key of the host build's test algorithm; None runs the sequence of the stack before COM-28,
    which has no service 27 (the setup rows are skipped).
    """
    import udsoncan
    from udsoncan.exceptions import NegativeResponseException

    answers: list[str | None] = []
    seed = b""
    for kind, text, _ in EXCHANGES:
        if kind == "setup" and key_of is None:
            answers.append(None)
            continue
        if text.endswith("KEY"):
            text = text[:-3] + key_of(seed).hex(" ")
        try:
            response = client.send_request(udsoncan.Request.from_payload(payload(text)))
        except NegativeResponseException as e:   # send_request raises on every negative response
            response = e.response
        raw = bytes(response.original_payload)
        if text == "27 01":
            seed = raw[2:]
        answers.append(raw.hex(" ").upper())
    return answers


def record(argv: list[str]) -> None:
    """Records the answers of the stack before COM-28 into did_responses_before_com28.json."""
    import test_app_uds_host as t

    t.ensure_dlls()
    app = t.HostApp(t.APP_DLL)
    app.power_on()
    bus = t.EcuBus(app, None)
    client = t.canflash.make_client(bus)
    client.open()
    try:
        answers = run(client)
    finally:
        client.close()
        bus.shutdown()
    rows = [{"kind": kind, "request": text, "response": answer}
            for (kind, text, _), answer in zip(EXCHANGES, answers)]
    GOLDEN.write_text(json.dumps({"recorded_on": argv[0] if argv else "", "rows": rows}, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    for row in rows:
        print(f"{row['kind']:8} {row['request']:28} -> {row['response']}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["record"]:
        record(sys.argv[2:])
    else:
        print(__doc__)
