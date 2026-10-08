# Contributing to neXtBSW

Thanks for looking. neXtBSW is a small project with a single maintainer, developed alongside real hardware. This page
says how to build it, how to check your change, and what a change has to carry to be accepted. It describes what
exists today — where something is still being set up, it says so.

## Get set up

Prerequisites: Python 3.12, CMake ≥ 3.28, Ninja, and `arm-none-eabi-gcc` for target builds. The standard is the
pinned xPack GCC 11.3.1 that `python -m tools.ci setup` fetches into the tool cache (versions in
[`tools/ci/versions.toml`](tools/ci/versions.toml)); another GCC 11 ARM toolchain on `PATH` or pointed to by
`NX_ARM_GCC_BIN` also works. After changing the compiler, configure an existing build directory once with
`cmake --preset <name> --fresh`: CMake keeps the old compiler path in its cache. Eclipse OpenBSW is a Git submodule and is never modified.

```sh
git submodule update --init
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -r tools/requirements.txt \
    -r external/openbsw/libs/bsw/middleware/tools/cpp_generator/requirements.txt
# working on the Studio? its packages are separate since TOOL-63:
.venv/Scripts/python -m pip install -r tools/requirements-studio.txt
```

On Windows, always use the interpreter from `.venv` and set `PYTHONUTF8=1`.

## Build and test

One command runs everything — all host tests and both target builds. It is the same command CI runs, and a change is
not ready before it is green:

```sh
.venv/Scripts/python tools/test --group all --jobs 4
```

Useful variants while working:

```sh
.venv/Scripts/python tools/test --list            # what would run
.venv/Scripts/python tools/test -k nxconfig       # a selection
.venv/Scripts/python tools/test --group target    # only the target builds
.venv/Scripts/python tools/test --slow            # including the long mutation runs (~11 min)
```

If a prerequisite is missing (no toolchain, no submodule), the affected group is **skipped and named** rather than
reported as a failure. Nothing in `tools/test` touches hardware: no J-Link, no CAN, no serial port, no MATLAB.
Details, including how the test list in `tools/test/tests.yaml` is kept complete, are in
[`tools/test/README.md`](tools/test/README.md).

A single target build, for example:

```sh
cmake --preset buildtest-s32k148-min
cmake --build --preset buildtest-s32k148-min       # must be warning-free, builds with -Werror
```

Two build profiles exist on purpose. `evalboard-s32k144-demo` is the board that really runs; `buildtest-s32k148-min`
is a second derivative that is only built. If the second one breaks, board neutrality is broken — that is the point of
having it.

Some console commands exist only in a test build, never in a standard build, because they break the running
system on purpose. They are switched on per build folder with a CMake option:

```sh
cmake --preset <preset> -B build/<preset>-hftest -DNX_HARDFAULT_TEST_COMMAND=ON   # hardfault udf|bus|sp, to check the fault dump
cmake --preset <preset> -B build/<preset>-gdtest -DNX_GD_ENABLE_COMMAND=ON        # <gd> enable of the gdTmc6200 module
```

The second one switches on a gate driver through its enable pin: use it **only with a person at the board**. Which
projects offer which command is listed in the [console reference](doc/reference/console.md).

The other way round, a product image leaves out every test and fault-injection command, also those of the standard
build (`safety`, the fault and shut-down commands of `lc`, `sbc failstart`), and refuses the test options above
(SYS-74, `cmake/NxProductBuild.cmake`):

```sh
cmake --preset <preset> -B build/<preset>-product -DNX_PRODUCT_BUILD=ON
.venv/Scripts/python tools/elfcheck product --app build/<preset>-product/ecu/application/RelWithDebInfo/app.ecu.elf
```

New hardware-independent code gets a host test. Register models are tested against a simulated device (see
`libs/bsw/sbcUja113x/test`), and a host test is expected to have been checked with deliberately introduced faults, so
that it demonstrably fails when the code is wrong.

## What a change should respect

- **`external/openbsw` is never modified.** Fixes for OpenBSW are prepared as upstream patches under
  [`doc/openbsw-beitraege/`](doc/openbsw-beitraege/README.md).
- **Generated code is not edited by hand.** Change the generator (`tools/bswgen`, `tools/rtegen`) or the model.
- **Board facts belong in configuration, not in code.** Pins, crystal, fitted parts, memory sizes and CAN instances go
  into the derivative (`mcudb/`), the board (`boards/<board>/board.yaml`) or the project
  (`projects/<project>/project.yaml`) — never as a constant in reusable code, in the bootloader or in a tool. The
  binding target picture is [`doc/architecture/README.md`](doc/architecture/README.md); its section 11 is the review
  checklist every change is measured against.
- **Code comments are English**, short, and in the style of the surrounding code.

## Language

**Published documentation is written in English.** This includes `README.md`, everything under `doc/` apart from the
changelog fragments and the internal planning files listed below, module documentation under `libs/*/doc/` and the
READMEs next to tools, boards and projects.
Reason: the software targets an international audience, and its technical vocabulary — AUTOSAR, OpenBSW, the
datasheets — is English anyway, so English documentation needs fewer invented translations.

**The changelog stays German.** `CHANGELOG.md` is the project's working log, not a product document: it records what
was done, why, with which measurements, and how it was verified. It is written and read by the maintainer, and
translating it would cost effort without a reader who benefits.

In short:

| Content | Language |
|---|---|
| `README.md`, `doc/**`, module and tool documentation, this page | English |
| `CHANGELOG.md` and the fragments in `doc/changelog.d/` | German |
| Code comments and identifiers | English |
| Commit messages | either, German in practice |
| Internal planning files (`doc/todo.yaml`, `doc/TODO.md`, `CLAUDE.md`) | German |

Existing German documents are being translated separately; do not translate one as a side effect of another change.
When you touch a document, write the new parts in the language the table above calls for.

## Submitting a change

The repository is not public yet, so there is no issue tracker or pull request queue to point at. Until there is,
changes reach the maintainer as patches or as a branch, and the expectations are the same:

1. One topic per change, with a message that says what changed and why.
2. `.venv/Scripts/python tools/test --group all` is green, or the skipped groups are named.
3. A changelog entry as a fragment `doc/changelog.d/<topic>.md`, in German, following the style of `CHANGELOG.md`:
   what, why, measured values, and **how it was verified — on host/mock or on the board**. Anything not verified is
   named as open. Fragments avoid merge conflicts in `CHANGELOG.md`; see
   `doc/changelog.d/README.md` (not published, available on request).
4. Documentation updated in the same change, if the change makes a document wrong.

Claims about hardware behaviour need to say where they come from. "Works" means it was measured; if it was only run
against a mock, say so.

## Developer Certificate of Origin

neXtBSW is being released under the **Apache License 2.0** (the licence decision is made, the `LICENSE` file and the
file-header rollout are still being prepared). Contributions are accepted under the same licence, certified with a
**Developer Certificate of Origin** (DCO) — the same lightweight mechanism Eclipse projects use. No copyright
assignment and no contributor licence agreement.

Add a sign-off line to each commit, with your real name and an address you can be reached at:

```
Signed-off-by: Jane Doe <jane@example.org>
```

`git commit -s` adds it for you. By signing off you state that you wrote the contribution or otherwise have the right
to submit it under the project's licence — the full text is at <https://developercertificate.org/>.

Please do not contribute material you cannot license this way. That includes code or data derived from documents under
a non-disclosure agreement and vendor files whose redistribution terms are unclear; such material is kept out of the
open source line on purpose.

## Not part of the open source project

**neXtBSW Studio** (`tools/studio`), the desktop tool for configuring projects, is proprietary. It lives in this
repository but is excluded from the open source release. Everything the Studio does is available on the command
line, and that is what this page and the documentation describe.
