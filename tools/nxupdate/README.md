<!-- SPDX-License-Identifier: Apache-2.0 -->

# nxupdate — moving a workspace's pin

TOOL-64 (`doc/architecture/customer-project.md` gap N9, section 5.5). Qt-free, command line, no compile.

A customer workspace pins exactly one neXtBSW release in `nextbsw.yaml` (`doc/architecture/customer-project.md`
section 3.1) and never updates it automatically. `nxupdate` is the one command that makes an update deliberate and
traceable: it generates every project of the workspace with the old and the new tree, checks the target release's
provenance (TOOL-65, gap N10), runs the new tree's format migrations (TOOL-66, gap N11), and writes a report of
what changed - in particular the changes that decide whether a fielded ECU can take the new software without a
bootloader update.

```sh
.venv/Scripts/python tools/nxupdate acme-bcm --to 1.5.0                    # dry run: report only, nothing changes
.venv/Scripts/python tools/nxupdate acme-bcm --to 1.5.0 --write            # moves the pin, writes the report
.venv/Scripts/python tools/nxupdate acme-bcm --to 1.5.0 --write --commit   # ... and commits it (never pushes)
.venv/Scripts/python tools/nxupdate acme-bcm --to 1.5.0 --release          # customer release: a development
                                                                           # target tag is an error, not a warning
.venv/Scripts/python tools/nxupdate acme-bcm --to v1.0.0-rc1               # a release candidate (TOOL-232)
```

**Release candidates (TOOL-232).** `--to` also takes a release candidate tag `vX.Y.Z-rcN` (Release R1 is
`v1.0.0-rc1`). It is used as the git ref of the target tree; the pin keeps `X.Y.Z`, because `nextbsw.version` has to
equal the tree's `VERSION` (ARCH-55), and the version part of the tag must be that `VERSION` - a candidate
`v1.0.1-rc1` on a tree with `VERSION` 1.0.0 is refused before anything moves, and so is any other spelling
(`v1.0.0-rc`, `v1.0.0-beta1`). The release rule (TOOL-65) checks the candidate tag like a release tag: annotated,
reachable from `master`, clean. Known limit until the rule moves into `tools/nxconfig` after R1: `nxconfig pin` on the
updated workspace still looks for `v<version>` and reports the candidate checkout as a development pin.

The default is a dry run: the workspace, its pin and its pinned tree are left exactly as they are, and the full
report goes to stdout. Nothing is written unless `--write` is given, and nothing is committed unless `--commit` is
given (which implies `--write`) - **`nxupdate` never pushes**, in any mode. This mirrors the project rule that only
an explicit switch commits or pushes on someone's behalf.

## What one run does (section 5.5)

1. **Generate "before"** every `project.yaml` of the workspace with the currently pinned tree (`bswgen`, `rtegen`; nothing is
   compiled).
2. **Move the pin.** The target tree is checked against the release rule first (TOOL-65: annotated tag `v<version>`, reachable
   from `master`, clean); a bad tag is refused before anything changes. A dry run uses a throwaway `git worktree` of the pinned
   tree at the target tag (no network, the real submodule is untouched); with `--write` the real submodule is checked out to the
   tag (`git submodule update --recursive`) and `nextbsw.yaml` is updated.
3. **Migrate.** `<target tree>/tools/nxconfig migrate <workspace>`: the *target* release's own migration engine, dry run
   unless `--write`. A migration only changes how a value is written, not what is generated.
4. **Generate "after"** with the target tree in a **staged copy** of the workspace (TOOL-215): its own files with a pin naming
   the target version and the target tree where the pin points (a `git worktree` at the tag; with `--tree` a directory link),
   because every tool checks that the workspace pins exactly that tree. Paths are mapped back in the report.
5. **Report:** the pin check's findings, skipped releases, a changelog excerpt, the migration's output, `nxconfig check`
   messages that appeared or disappeared, and the generated-file diff with the field-relevant files marked. With `--write` it is
   saved to `doc/nextbsw-updates/v<old>-v<new>.md` in the workspace (customer-project.md section 3.2); a dry run prints it (and
   writes `--report <file>` if given).
6. **Commit** (`--commit` only): the pin, the submodule gitlink, the migration's changes and the report as one commit; never a
   push.

## Field-relevant changes

Section 5.5 asks for a prominent list of field-relevant changes (memory layout, bootloader identity, CAN IDs, diagnostic
addresses, record formats): those decide whether a fielded ECU can take the new software without a bootloader update. No
registry says which generated file holds which of these, so `field_relevance()` in `update.py` is a deliberately
over-inclusive **heuristic**: a **path** hint (memory/flash layout, bootloader, CAN, diagnostics, NVM) flags the whole file,
a **content** hint (a changed line matches a `CAN_ID_`, `DID_`, `FLASH_...`, `BOOTLOADER_...` or `NVM_...` identifier) flags
it even without a path clue. A false positive costs one look, a false negative could cost a bootloader update in the field.
Extend `FIELD_PATH_HINTS`/`FIELD_CONTENT_HINTS` rather than special-casing a board or project.

## `--tree`: the `source: archive` fallback

`nxupdate` does not download or unpack a release archive (`nextbsw.yaml` `source: archive`,
customer-project.md 4.4) - that stays a manual step. `--tree <path>` uses an already-materialised tree as the
target as it is, for either pin source. Without `--tree` and with `source: submodule` (the recommended variant),
the target is resolved from the pinned tree's own git history at `v<version>`.

## Design notes (see `update.py`'s module docstring)

- Generation and migration run as a subprocess against an explicit tree path (`python <tree>/tools/bswgen/bswgen.py ...`):
  a tool runs with the generator code of the release it is judging (customer-project.md 5.4, N7). Only the release rule
  (`pincheck`) is called in-process.
- A dry run touches nothing; the "after" tree is a `git worktree`, not a clone.
- On a refused tag with `--write` the submodule may already be at the new commit while `nextbsw.yaml` still names the old
  version; `nxconfig pin`/`check` report that mismatch and the report says so.

## Host tests

`tools/nxupdate/test_nxupdate.py` (`tools/test -k nxupdate`) builds an invented upstream repository with invented releases and
stub `bswgen`/`rtegen` scripts in `%TEMP%` (no network); one test points the real migration step at this repository's
`tools/nxconfig` (read-only). `tools/nxupdate/test_nxupdate_workspace.py` (`-k nxupdate-workspace`, TOOL-215) uses a local bare
clone of this repository with two invented annotated releases, a customer repository with the tree as a submodule and a project
with its own board outside the tree: a dry run from a relative path, a dry run with `--tree` and `--write --commit` with the
real generators.

## Open points

- A Studio dialog around `nxupdate` (customer-project.md N9) is a later task.
- The command stops at generation: no compile and no workspace tests (`tools/ci run --workspace <dir>`, TOOL-67, does that;
  chaining it is a follow-up) and no ELF size change (the report says so).
- The comparison of `nxconfig check` messages is skipped for a project when either tree has no `tools/nxconfig`.
- Signed tags and a real release mirror are out of scope (N15, Q2).
