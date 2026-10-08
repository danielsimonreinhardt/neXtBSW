# tools/canflash – application and bootloader over CAN (UDS on ISO-TP)

```sh
.venv/Scripts/python tools/canflash/canflash.py flash <app.hex> [--interface vector --channel 0]
.venv/Scripts/python tools/canflash/canflash.py info    --project projects/<project>/project.yaml
.venv/Scripts/python tools/canflash/canflash.py compat  <app.hex>
.venv/Scripts/python tools/canflash/canflash.py scan    [--projects projects/a --projects projects/b]
.venv/Scripts/python tools/canflash/canflash.py flash   <app.hex> --ecu <project name>
.venv/Scripts/python tools/canflash/canflash.py fingerprint [--write] [--tester <name>]  # BL-04, DID 0xF184
.venv/Scripts/python tools/canflash/canflash.py record  <app.hex> <record.hex>          # validity record for J-Link
.venv/Scripts/python tools/canflash/canflash.py blimage <bootloader.hex> --c-out ...    # in the updater build
.venv/Scripts/python tools/canflash/canflash.py blupdate <blupdater.hex> [--app <app.hex>]   # only with the user on site
```

CMake: `cmake --build --preset <preset> --target flash-can` (adapter via `CAN_INTERFACE`/`CAN_CHANNEL`).

Errors of the UDS conversation end with one line, not a traceback (BL-25): a target that stops answering gives
`canflash: no response in time (...)` and exit code 1. `blupdate --app` whose application flash fails after the bootloader
was replaced says `Bootloader updated to <version>, application NOT flashed: <reason>` and exits with code 5; the ECU then
waits in the new bootloader for `canflash flash <app.hex>`.

`blupdate` asks for `UPDATE` on the terminal; without a terminal and without `--yes` it stops with `needs --yes when not
interactive` before anything is sent (TOOL-53). An updater built for another CAN bus (`-DBOOTLOADER_CAN_BUS=CAN1`) reports
on its own bus: `blupdate --channel 0 --follow-channel 1` follows it (`--follow-bitrate` for another bit rate; BL-39).

An image that does not belong to the target is refused before the erase, and `flash` then resets the ECU (`11 01`) so the
installed application starts again if it is valid (TOOL-53); `--stay-in-bootloader` leaves it in the bootloader.
`--skip-local-compat-check` is a **test aid** (warning on every run): the foreign image is handed to the bootloader's own
check (BL-24 c); an image the local check found different is never flashed.

## Reaching the bootloader: `--wait <s>`

Without `--wait`, `flash` switches a running application into the bootloader itself (UDS `10 02`, repeated until the
bootloader answers); this is what the CMake target `flash-can` uses (COM-09). `--wait <s>` is for a tester that resets or
power-cycles the board by hand: the tool waits up to `<s>` seconds for the bootloader after the reset (default 0). The option
is also accepted by `info`, `compat`, `scan`, `fingerprint` and `blupdate`.

While waiting, `10 02` goes out every 10 ms so the bootloader's startup window (`BL_STARTUP_WINDOW_MS`, 200 ms on the eval
board) gets several requests. Without an acknowledging node the adapter's transmit queue fills and the Vector driver refuses
frames (`XL_ERR_QUEUE_IS_FULL`); since TOOL-51 canflash empties the queue after a refusal (python-can's Vector backend sends a
high-priority frame, ID 0), keeps requesting and reports at the end how many requests were refused.

### Way back from a held safe state (e.g. `ROM_CHECK_FAILURE`)

An ECU that holds its safe state until a power cycle (SYS-23, S1 held) does not answer `10 02` and cannot be reset
over the bus. The way back is a power cycle while canflash waits:

```sh
# by hand: start, then switch the ECU off and on within the 30 s
.venv/Scripts/python tools/canflash/canflash.py flash <app.hex> --wait 30
# or canflash switches a LabControl power supply itself (TOOL-51, optional)
.venv/Scripts/python tools/canflash/canflash.py flash <app.hex> --wait 30 --power-cycle psu:COM6 [--power-on-current 0.5]
# or any other switch as shell commands
.venv/Scripts/python tools/canflash/canflash.py flash <app.hex> --wait 30 --power-off-cmd "<off>" --power-on-cmd "<on>"
```

The bootloader is caught in its startup window after power-on and the application is flashed in the same run. The
preprogramming step before the wait (10 03, 85 02, 28 03) goes unanswered and costs about 3 s; `--no-prepare` skips it.

Power options (only together with `--wait`; without them nothing is switched):

| Option | Meaning |
|---|---|
| `--power-cycle <device>` | LabControl power supply (device id as in LabControl, e.g. `psu:COM6`): `PSU_OUT_OFF`, pause, `PSU_OUT_ON` over LabControl's network interface (`LABCONTROL_URL`, default `http://127.0.0.1:8420`, and `LABCONTROL_TOKEN`, like the LabControl MCP server). Standard library only, no dependency on LabControl. LabControl decides about permission and limits; a refusal is reported with its error code. Without `LABCONTROL_TOKEN` (or with a wrong one) LabControl refuses with "unauthorized" (HTTP 401, TOOL-153); canflash then names the next step (set `LABCONTROL_TOKEN`) instead of just the code - the token itself comes from LabControl (Settings -> Network, copy button), canflash never prints it and sends it only to LabControl |
| `--power-on-current <A>` | switch on with this current limit (`PSU_CURR`) instead of `PSU_OUT_ON`, which raises the limit of an HCS-34xx to only 0.1 A |
| `--power-off-cmd <cmd>` / `--power-on-cmd <cmd>` | any other switch (relay, other supply) as two shell commands; exit code other than 0 is an error |
| `--power-off-time <s>` | seconds without power (default 1.0) |

The switch runs in a thread of its own while the requests go on. A switch that fails is reported and the waiting goes on, so the
ECU can still be switched by hand; a Ctrl-C during the pause switches the ECU on again before canflash ends.

## Where the values come from

No value of the target is a constant of the tool (ARCH-08, COM-11, TOOL-05):

| Value | Source (in this order) |
|---|---|
| memory layout, board/derivative identification | `--layout <build>/generated/board/bootloader/bl_memory.h`, `--project`, `--ecu`, `NX_BL_LAYOUT`/`NX_PROJECT`, build directory of the hex file |
| name of the master key without `bl_security.h` next to `bl_memory.h` (TOOL-204) | `--key-name <name>`, `NX_BL_KEY_NAME`; only a bootloader with HMAC security access needs it |
| diagnostic addresses, ISO-TP, bootloader identification | `bl_diag.h` next to `bl_memory.h`, or project `diagnostics` |
| bit rate, sample point, SJW | `--bitrate`/`--sample-point`/`--sjw`, otherwise project `can.<bootloader.can>` (also via `NX_PROJECT` of the build's CMake cache), otherwise 500 kbit/s with the driver default |
| time quanta of the adapter | `--quanta` (default 16; Vector: TSEG2 = quanta × (100 − sample point) %) |
| MCU family, image rule for `blimage`/`blupdate` (BL-08) | `BL_FAMILY` in `bl_memory.h` (host layouts, proposal for blgen), project (`--project`/`--ecu`), `NX_FAMILY` from `generated/nxconfig/project.cmake` or the project of the build; without any of these, the only family with an updater port (with a notice). Rule per family in `IMAGE_RULES` (s32k1xx: FCF and vector table, like `bootloader/platforms/s32k1xx/upd_family_check_image.inc`); a family without a rule is rejected |

Every command gets its UDS client from `make_client()`, which sends each request **blocking**: the client's P2 (1.0 s; P2\* =
`BL_P2STAR_SERVER_MS` + 1 s) starts when the last consecutive frame is out, as ISO 14229-2 counts it (TOOL-48; before, P2 ran
while the frames went out and a 1026-byte block took more than 1 s with STmin 1 ms). The bootloader's N_Cr
(`diagnostics.isotp.n_cr_ms`) still limits the gap between two frames; a request the stack cannot send ends like a missing
response ("not sent completely").

## Options from TOOL-05

- `--progress auto|line|steps|off` (flash, blupdate): `auto` writes a running line on a terminal and one line per 10 % into a
  pipe, the CMake log or the Studio output.
- **Version check** before erasing: the bootloader identification (DID 0xF180) is shown; a prefix other than
  `diagnostics.bootloader.version_prefix` (case-sensitive, PLAT-51; the former `S32K14x-BL` is accepted) or another major
  version than `BL_VERSION_NUMBER` is a warning. Board, derivative and memory layout (DID 0xF1A0, routine 0xF1A0) remain
  hard errors.
- **Compatibility routine (BL-24):** 0xF1A0 from bootloader 0.3.4 on, 0xFF02 below (`compat_routine_of()`, by the version in
  DID 0xF180); a target that names no version gets 0xF1A0 first and 0xFF02 after a requestOutOfRange (0x31).
- `scan`: TesterPresent (`3E 00`) to every ECU of the projects (`--projects`, repeatable, default `projects/`) and once per
  functional address; shows who answers, whether bootloader or application runs (DID 0xF181), and answers on IDs of no
  project. Projects with the same request **and** response IDs are alternatives for the same device.
- **Fingerprint and identifications (BL-04):** `flash` writes DID 0xF184 (date in BCD and tester identification) before
  erasing; without it the bootloader answers 0x22. The tester identification is `--tester <name>`, else `NX_TESTER_ID`,
  else `canflash`. `info` shows the software version (DID 0xF189) and hardware number (DID 0xF191) and warns if the target
  belongs to other hardware than the project. `fingerprint [--write]` reads (or first writes) it.
- **Who answers (TOOL-45):** `info` is read-only and asks DID 0xF181 first, then prints `application active: ...` or
  `bootloader active: ...`; with the application running the bootloader's own DIDs are not readable. `info --bootloader`
  switches into the bootloader (10 02), reads and resets back (11 01).
- `--ecu <project name>` (flash, info, compat, blupdate) selects the project and with it addresses and memory layout; a hex
  file whose build has other diagnostic addresses is rejected before the bus is opened.

## Security access and keys (BL-03)

The bootloader answers a 16-byte seed with `HMAC-SHA256(device key, seed)` (32-byte key). The device key is
`HMAC-SHA256(master key, chip UID)`; it is written once per device into the key sector (the flash sector below the
validity record, `BL_KEY_ADDR`) over the debugger. The bootloader image is the same for every device and holds no
key. The project only names the master key (`diagnostics.bootloader.security.key`, default: the board name); the
key itself stays on the workstation:

| Where | Use |
|---|---|
| `NX_BL_KEY` (64 hex digits) or `NX_BL_KEY_FILE` (path) | CI, neXtBSW Studio |
| `%LOCALAPPDATA%/neXtBSW/keys/<name>.key` (or `NX_BL_KEY_DIR`) | workstation |
| `tools/canflash/keys/nextbsw-insecure-test.key` | **insecure**, host tests and CI only; a project must set `allow_insecure_test_key: true`, provisioning refuses it otherwise |

```sh
.venv/Scripts/python tools/canflash/canflash.py keygen <name>          # new random master key, never overwrites
.venv/Scripts/python tools/canflash/provision.py --project projects/<p>/project.yaml --uid-from-jlink   # dry run
.venv/Scripts/python tools/canflash/provision.py --project projects/<p>/project.yaml --uid-from-jlink --run
```

`provision.py` reads the chip UID (`--uid <hex>`, `--uid-from-can` = DID 0xF18C, or `--uid-from-jlink` = the UID
registers of the derivative, `mcudb uid`), writes the key record as hex file plus a J-Link script (`loadfile` of the
key sector only, `verifybin`) and runs it only with `--run`. `canflash` reads the UID itself (DID 0xF18C), derives
the device key and answers the seed; `info` shows method, key state (DID 0xF1A1) and where the master key was
found. Without a provisioned key the bootloader refuses the security access (NRC 0x22) and canflash says why.

Transition: a bootloader before BL-03 (identification `S32K14x-BL`, or no DID 0xF1A1) is still answered with the
old XOR/rotl13 (4-byte seed and key, `blkeys.legacy_key`), so the board can be updated with the old bootloader.

### Security levels of the application (COM-30)

The application's levels (`diag.yaml security[]`, method `hmac-sha256`, the default) use the **same device key**
from the same key sector - nothing else has to be provisioned. The key of a level is the first `key_bytes` of
`HMAC-SHA256(device key, "NXAK" || requestSeed || seed)` (`blkeys.app_key`); label and level keep it from unlocking
another level or the bootloader. `unlock` opens the level's session, reads the chip UID from the application (an
identifier with `source: {bsw: chip.uid}`, usually 0xF18C) or takes `--uid`, unlocks, then writes and reads:

```sh
.venv/Scripts/python tools/canflash/canflash.py unlock --project projects/<p>/project.yaml \
    --write FD00=12345678 --read FD00            # --level <name|0x01>, --uid <hex> without chip.uid identifier
```

## Sector 0, the flash configuration field and CSEc (NVM-15)

On s32k1xx the chip reads its security byte FSEC out of reset from the flash configuration field `0x400..0x40F` in sector 0;
erased (`0xFF`) it means **secured** (no debugger access). Once the CSEc has taken a key space (`PGMPART` with key size != 0,
e.g. `nvm.partition.key_slots: 20`), **mass erase is locked**, and a reset while the field is erased loses the part. The only
way back is NVM-13: load the `MASTER_ECU_KEY` and run `DBG_CHAL`/`DBG_AUTH`.

Rules the tools follow:

- **Whoever erases sector 0 writes the field again, unsecured, before any reset can come.** The bootloader image carries the
  field (FSEC `0xFE`, FOPT `0x7F`; checked by `tools/elfcheck`); the updater programs the field of its stub right after
  each erase of sector 0. `blupdate` refuses an updater image without a field for its stub and an embedded bootloader whose
  field is not the reference. The application never writes sector 0.
- **No mass erase on a part that may be CSEc-partitioned.** `flashguard.py` checks debugger runs before they start: a mass
  erase (J-Link `erase`/`unlock`, OpenOCD `mass_erase`, pyOCD `--chip`, CubeProgrammer `-e all`) is **refused** when the
  project reserves key slots or a partition code says partitioned, and **warned** about while the state is unknown on a
  derivative with CSEc; a J-Link range erase of sector 0 and an image for sector 0 without a valid field are always refused.
  `provision.py` runs it on its scripts before J-Link.
- `blupdate` on such a part shows the risk with the confirmation (and with `--yes`): keep the supply stable, do not reset.

```sh
.venv/Scripts/python tools/canflash/flashguard.py --project projects/<p>/project.yaml \
    --script <build>/bootloader/RelWithDebInfo/flash-bootloader.jlink --image <build>/bootloader/RelWithDebInfo/bootloader.hex
.venv/Scripts/python tools/canflash/flashguard.py --project projects/<p>/project.yaml --depart 0xF --script my.jlink
```

`--depart` is the partition code read from the part (`flexnvm show`, SIM_FCFG1 DEPART); `0xF` = not partitioned. The key space
cannot be read on s32k1xx (NVM-10), so a partitioned part counts as locked. J-Link's own unlock dialog is refused by a
CSEc-partitioned chip; go the NVM-13 way.

## Host tests

| File | Content |
|---|---|
| `test_flashguard.py` | NVM-15: configuration field rule with counter-checks, CSEc risk from project/derivative/partition code, mass erase in command texts, flash targets of `CMakeLists.txt` and `provision.py`, `blupdate` stub field and CSEc notice |
| `test_canflash_tool.py` | progress, version warnings, silent DID probe (with counter-check without it), bus timing from the project, ECU selection, scan on a virtual bus – without the DLL, a few seconds |
| `test_bootloader_host.py` | against the bootloader core as a DLL; TOOL-05: old bootloader, progress in the log, foreign prefix, two ECUs (demo and S32K148) on one bus |
| `test_bl_update_host.py`, `test_bl_update_mutations.py` | updater, power loss, meaningfulness |
| `test_bl_security_host.py` | BL-03: SHA-256/HMAC against NIST and RFC 4231 vectors, security access (wrong key, repetition, lockout also across a reset, missing or damaged key record, other UID) for three layouts, transition to XOR/rotl13, key store, `provision.py` without hardware |
