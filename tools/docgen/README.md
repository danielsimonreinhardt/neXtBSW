# docgen - generated reference documentation

Writes the configuration reference in [`doc/reference/`](../../doc/reference/README.md) from the pydantic schema in
[`tools/nxconfig/model.py`](../nxconfig/model.py): one page per configuration level (derivative, board, project,
ECU description) with every key, its type, unit, required/optional, default, allowed values and description. The
pages are checked in; a host test fails when schema and pages disagree.

It also writes the module catalogue [`doc/reference/modules.md`](../../doc/reference/modules.md) (DOC-04): one entry
per directory in `libs/bsw` and `libs/bsp`, and one per module with a manifest in a port (`platforms/<family>/port`,
`ecu/platforms/<board>` - the places `tools/nxconfig` reads port modules from, DOC-16), collected from the module
manifest (`module.yaml`, or a parked `module.yaml.pending`), the module documentation `doc/<module>.md`, the public
headers and [`tools/test/tests.yaml`](../test/tests.yaml): an entry is a host test of a module if one of its
`covers` lies in the module directory or if it names the module in its optional field `modules` (DOC-18, for tests
outside the module directory, such as the generator host tests of the port modules). The state of verification is only
taken from the manifest fields `verification` and `verification_note` (DOC-14); without them the page says *not
stated*. The counts in the introduction and in `README.md` and the section *Gaps* (modules without manifest,
verification, documentation or host test) are computed from the same data, so no sentence goes stale.

And it writes the console reference [`doc/reference/console.md`](../../doc/reference/console.md) (DOC-09): every
console command group the application can register, collected from the registration in the C++ sources
(`DEFINE_COMMAND_GROUP_GET_INFO_BEGIN`/`COMMAND_GROUP_COMMAND`, the `PlainCommandInfo` tables of per-device groups,
`ParentCommand`/`SimpleCommand` members, OpenBSW testers that a neXtBSW source declares, and `help`). Parameters
come from the help text (`param: ...`, `Usage: ...`); availability from `#ifdef`s around the registration, the CMake
conditions of the source file (`IN_LIST NX_MODULES`, `option(NX_...)`, `PLATFORM_SUPPORT_*` of the board port), the
module and family directory; per-device group names from the manifests and, per project, from `tools/nxconfig`;
board rules from the help text (`console.MARKERS`) and from the list "Only with a person at the board:" in
`boards/<board>/README.md`. It needs `external/openbsw` checked out (exit code 2 otherwise).

Outside `doc/reference` it writes the module map [`doc/figures/07-modulkarte.md`](../../doc/figures/07-modulkarte.md)
(DOC-64): from the same module list as `modules.md`, a Mermaid figure with one node per module (grouped by layer or
port and by `kind`, colour = `verification`) and a status table with a symbol per verification, legend in German.
`--check` compares it like the reference pages; `--figures <dir>` writes it elsewhere (with another `--out` it
follows `--out`).

```sh
.venv/Scripts/python tools/docgen            # rewrite doc/reference and the module map (only files that changed)
.venv/Scripts/python tools/docgen --check    # exit 1 with a diff if a page is stale
.venv/Scripts/python tools/docgen --gaps     # schema fields without description or unit, and module gaps
.venv/Scripts/python tools/docgen/test_docgen.py
.venv/Scripts/python tools/docgen/test_console.py
```

After changing the schema - including a comment in `model.py`, because comments are where most descriptions come
from today - run the first command and commit the changed pages together with the schema.

| File | Content |
|---|---|
| `schema.py` | model → sections and fields (data classes, no Qt, no file access; usable from neXtBSW Studio) |
| `render.py` | Markdown tables, contents, escaping |
| `reference.py` | the pages of `doc/reference`, write, check, gap list |
| `modules.py` | module catalogue: collect `libs/bsw`, `libs/bsp`, the port modules and the test list, render `modules.md` with its section *Gaps*, module gaps |
| `console.py` | console reference: scan the command registration, CMake conditions, projects, board rules; render `console.md`, console gaps |
| `modulemap.py` | module map `doc/figures/07-modulkarte.md`: Mermaid figure and status table from the module catalogue |
| `__main__.py` | command line |
| `test_docgen.py` | host test with its own small schema, plus the check against the real one |
| `test_console.py` | host test of the console reference with its own small source tree, plus the real sources |
| `site.py`, `mkdocs_hooks.py` | documentation website: pages of the open profile staged for MkDocs, build from the export ([website.md](website.md)) |
| `test_site.py` | strict build of the website from the export, with counter-tests (tests.yaml `docgen-site`) |

Short forms on the pages (DOC-76; all anchors stay valid): an object whose table and subtree equal an earlier one is
written as a pointer (`render.signature`, `reference.layout`); a description of 15 words or more that repeats on a
page is written once and then pointed to (`render.REPEAT_WORDS`); the objects of the power stage and motor control
(`reference.AFTER_R1`, per page and path) and the modules `gdTmc6200`, `bccMc33771`, `mcuLink`
(`modules.AFTER_R1`) get a short entry marked *after R1*. To bring one back, remove it from that list. `modules.md`
leaves out what its overview table already says (host test lists, documentation link, empty configuration lines).

Descriptions are taken from `Field(description=...)`, else the field's comment in the model source, else the first
paragraph of the docstring of its object type. Units come from `Field(json_schema_extra={"unit": ...})` or the naming
convention `<name>_<unit>` (`UNIT_SUFFIXES` in `schema.py`). Nothing is filled in by hand: where the schema says
nothing, the page shows the gap.

## Documentation website

`site.py`, `mkdocs_hooks.py` and `test_site.py` build the same sources into a website with MkDocs Material
(DOC-71, configuration [`mkdocs.yml`](../../mkdocs.yml)). How it works, how to build it and what the test checks:
[website.md](website.md) (German, like all new texts until DOC-70).
