# Board README template

Every board folder has two files: `board.yaml`, which describes the hardware as data, and `README.md`, which tells the
person at the bench what the data cannot. This page gives you a template for the README, the rules behind it, and a
filled-in example.

## What the README is for

`board.yaml` answers *what is fitted and how it is wired* — `nxconfig` and the generators read it
([Your first board](your-first-board.md#step-3-the-board)). Some knowledge about a board is not a configuration value
but still decides whether a session at the bench ends well:

- which actions can cut the supply, damage hardware or cannot be undone, and therefore need a person at the board;
- how the debug probe is connected, and how to get the board back when an image does not start;
- where the assembled board differs from its schematic;
- how far each of these facts has been confirmed, and when.

That goes into `boards/<board>/README.md`. The README does **not** repeat the pin tables of `board.yaml`; it names
entries of `board.yaml` where it needs them, so there is only one place for each value.

## Rules for the README

- **English**, like all published documentation ([`../../CONTRIBUTING.md`](../../CONTRIBUTING.md#language)).
- **The safety section is never deleted.** If you know of no rule, write "None known" and say what you checked. A
  board that exists only to be built says so in one line, and that is its safety rule.
- **Every rule says why.** A rule without its reason gets broken the first time it is inconvenient. Name the effect
  ("switches off the MCU supply"), and where it was found (a document, a measurement, a task).
- **Say what is confirmed.** Mark facts taken from a schematic you could only read as a picture, or from a document you
  could not get, the same way `board.yaml` does with `verify: true`, and date the state of the safety rules.
- **No workstation settings.** Serial port numbers, probe serial numbers and install paths belong to a computer, not to
  the board ([`../architecture/README.md`](../architecture/README.md), section 4: "Arbeitsplatz"). Write "the serial
  port of the USB-to-serial converter", not a port name.
- **Public sources only.** Cite data sheets, reference manuals and schematics by name and page. Content from a
  document its maker has not published does not go into the README, and such documents are not checked in (the rule
  for `mcudb/` is the same, see [Adding a derivative](new-derivative.md#the-rule-on-sources)).
- **Board-neutral wording.** The template has to fit a board with a system basis chip, one with a plain linear
  regulator, and a build-only profile. Remove rows that do not apply; do not invent them.

## The template

Copy everything between the two `~~~` lines into `boards/<board>/README.md` and fill in the angle brackets. Comments
in `<!-- … -->` are instructions; delete them when you are done.

~~~markdown
# <Board name> (<MCU> <package>)

<!-- One paragraph: what the board is, who made it, which revision this README describes, and its purpose in
     neXtBSW (running target, bring-up pending, build-only profile). -->
<Board name>, revision <rev>, with <MCU> in <package>. Hardware description: [`board.yaml`](board.yaml).
Schematic: <document, revision, where it can be obtained>. Projects on this board: <projects/...>.

## Status

<!-- What runs, what only builds, what was measured on the board and when. Link the task or changelog entry. -->
| Item | State | Confirmed by | Date |
|---|---|---|---|
| Configuration (`nxconfig check`) | <ok / not yet> | host run | <date> |
| Target build | <builds / does not link / not supported by the family yet> | host run | <date> |
| Bring-up on hardware | <running / pending / no hardware> | <measurement, task> | <date> |

## Fitted parts

<!-- Only parts the software talks to or that constrain it (supply, reset, transceivers, drivers, sensors).
     Name the entry of board.yaml (devices[].name) so the reader can find the wiring. -->
| Reference | Part | Function | `board.yaml` entry | Source |
|---|---|---|---|---|
| <U1> | <part number> | <function> | `devices: <NAME>` | <data sheet, revision> |

## Supply

<!-- Input voltage range, what feeds the MCU, and above all: what can switch the MCU supply off (a system basis
     chip, a power switch, a supervisor). If nothing can, say so. -->
- Input: <range, connector>
- MCU supply: <regulator / SBC output>, <voltage>
- Can software switch off the MCU supply? <no / yes: which part, which command or register>

## Debug set-up

<!-- Probe, interface, clock of the debug interface, connector, jumpers that must be in a given position. The probe
     and interface are also in board.yaml (board.debug); do not repeat install paths. -->
- Probe and interface: <probe> over <SWD/JTAG> at <kHz>, connector <Jx>
- Jumpers for debugging: <jumper: position, why>
- **Rescue path** when an image does not start: <how the probe regains the core (connect under reset, boot pin,
  mass erase), and how the bootloader is restored>

## Console and CAN

<!-- Name the board.yaml entries; say what has to be attached outside the board. -->
- Console: `uart: <NAME>` (<instance>), reached through <USB-to-serial converter / probe virtual COM port / header>.
  Baud rate: set by the project.
- CAN: one line per `can:` entry — transceiver (`phy`), connector, termination on the board yes/no, what an adapter
  needs (external transceiver, common ground).

## Safety rules

State of these rules: <date>. <!-- Update the date whenever a rule changes. -->

### Never without a person at the board

<!-- Actions that can cut the supply, drive a load or a power stage, or leave the board unreachable. For each:
     the action, the effect, the condition under which it becomes allowed. -->
- <action> — <what happens> — allowed only <condition>.

### Irreversible or hard to undo

<!-- Examples of what belongs here: flash partitioning that only a mass erase undoes; one-time or few-times
     programmable memories (fuses, OTP areas, non-volatile configuration of an SBC or PMIC); flash security or
     protection bytes that can lock out the debugger; replacing the bootloader in the field. -->
- <action> — <why it cannot be taken back> — <rule>.

### Debugging pitfalls

<!-- Behaviour that surprises: a jumper that lets an external part count resets, a watchdog outside the MCU that
     keeps running while the core is halted, a probe that resets and halts the core on connect, licence dialogs of
     debug software that block scripted runs. -->
- <pitfall> — <what you see> — <what to do>.

## Schematic versus assembled board

<!-- Every place where the assembled board differs from the schematic, and every schematic value that could not be
     read. Say where board.yaml records it (entry, comment, verify: true). -->
| Item | Schematic says | Assembled / measured | Recorded in `board.yaml` | Confirmed by, date |
|---|---|---|---|---|
| <item> | <value> | <value> | <entry> | <measurement> |

## Check and build

```sh
.venv/Scripts/python tools/nxconfig check projects/<project>/project.yaml
cmake --preset <preset>
cmake --build --preset <preset>
```

## Sources

- <schematic, revision>
- <data sheets of the fitted parts, revision>
- <board user manual, if the board is a vendor board>
~~~

## Example

A filled-in README in this tree: [`boards/buildtest_s32k148/README.md`](../../boards/buildtest_s32k148/README.md), a
profile with no hardware behind it, which states that in one line: no flashing, no debugging, no console, no CAN
access. For a real board the template asks for the same habits in more places: facts that are only assumed stay
visible as such (`unconfirmed`, `verify: true`, a dash in the "confirmed" column), a rule such as "never program the
non-volatile configuration of the chip" carries its reason, and a board where nothing in software can cut the supply
says so in one short safety section.

## Where the README fits in

- The data of the board: [Your first board](your-first-board.md#step-3-the-board).
- The build and flash targets the README refers to: [Build and flash](build-and-flash.md).
- A derivative that is not in `mcudb/` yet: [Adding a derivative](new-derivative.md).
