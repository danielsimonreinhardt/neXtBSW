"""Configuration and generator identity of a project (PLAT-79, doc/safety/tool-classification.md proposal C, TF4).

Answers "which configuration and which generators made this image?" without the build: two SHA-256 sums over
files and one identity derived from both.

  configuration  every file of the project's configuration the generators read: project.yaml, board.yaml, the
                 derivative of the mcudb and the files it refers to, the ECU description (rte.yaml) with its DBC,
                 the component descriptions (swc.yaml, ARXML, model hash, <Name>_data.m) and the calibration
                 dataset, the diagnostic description (diag.yaml), the BSW task manifest, the manifests of the used
                 modules, code files constants were read from, the workspace pin
  generators     the sources and templates of nxconfig, bswgen (with blgen), rtegen, dbcgen, diaggen, the parts of
                 tools/swc, tools/calgen and mcudb/tools they import, and the VERSION of the tree
  identity       SHA-256 over the two lines "configuration <sum>" and "generators <sum>"

Each sum is the SHA-256 of a sha256sum-style listing ("<sha256 of the file>  <label>\\n", sorted by label). A label
never holds an absolute path - a file of the project folder is "project/<path>", a file of the neXtBSW tree
"tree/<path>", any other file its path relative to the project folder - so the same files give the same identity
in every checkout and on every machine. Text files count with LF line ends (a checkout with CRLF gives the same
sum); no time, no user, no git state enters (the commit is the separate value nextbsw.version, 0xF195).

The image carries the identity as a constant (identity/nxIdentityFields.h, written by bswgen): the console shows
it ("version": first 16 hex digits, "info": all values) and the diagnosis reads it (configuration.identity, DID
0xF1F1 of the standard description). `nxconfig identity <project>` prints the same values without building.
Every generator run also writes a manifest (MANIFEST_DIR/<generator>.sha256) with the identity and the SHA-256 of
every file it wrote: each generated file is tied to the version and the inputs it was made from.

Standard library only, no Qt (Studio core).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from . import config as nxcfg
from .version import VersionError, read_version

# Short form shown in the one-line "version" output of the console (and as prefix of the 64-digit identity)
SHORT_DIGITS = 16
# Folder of the generated identity files below each generator's output directory
MANIFEST_DIR = "identity"
FIELDS_HEADER = f"{MANIFEST_DIR}/nxIdentityFields.h"

# Sources of the generators, relative to the tree the tools run from: (folder, patterns, recursive). Test files
# (test_*.py, folders named test) are not part of a generator.
GENERATOR_SOURCES = (
    ("tools/nxconfig", ("*.py",), False),
    ("tools/bswgen", ("*.py",), False),
    ("tools/bswgen/templates", ("*",), True),
    ("tools/rtegen", ("*.py",), False),
    ("tools/rtegen/templates", ("*",), True),
    ("tools/dbcgen", ("*.py",), False),
    ("tools/dbcgen/templates", ("*",), True),
    ("tools/diaggen", ("*.py", "*.yaml"), False),
    ("tools/swc", ("swc_yaml.py", "swc_skeleton.py", "mdata.py"), False),
    ("tools/calgen", ("caldata.py",), False),
    ("mcudb/tools", ("svd2header.py",), False),
)
VERSION_FILE = "VERSION"
TASKS_MANIFEST = "ecu/asyncCoreConfiguration/tasks.yaml"
CALIBRATION_DIR = "calibration"          # rtegen dataset_of(): <project dir>/calibration


@dataclass(frozen=True)
class Input:
    label: str
    file: Path
    sha256: str


@dataclass(frozen=True)
class Identity:
    """Identity of one project in one tree; see the module documentation."""

    version: str                         # neXtBSW version of the tree the generators run from ("?" if unknown)
    configuration: tuple[Input, ...]
    generators: tuple[Input, ...]

    @property
    def configuration_sha256(self) -> str:
        return digest(self.configuration)

    @property
    def generators_sha256(self) -> str:
        return digest(self.generators)

    @property
    def identity(self) -> str:
        text = f"configuration {self.configuration_sha256}\ngenerators {self.generators_sha256}\n"
        return hashlib.sha256(text.encode("ascii")).hexdigest()

    @property
    def short(self) -> str:
        return self.identity[:SHORT_DIGITS]

    def fields(self) -> list[tuple[str, str, bool]]:
        """(name, text, in the version line) of the image, in the order the console prints them. Names fit the
        name column of "info" (identity/IdentityText.h INFO_NAME_COLUMN, 15 characters with the gap)."""
        return [
            ("configuration", self.short, True),
            ("identity", self.identity, False),
            ("config inputs", f"sha256 {self.configuration_sha256[:SHORT_DIGITS]}, {len(self.configuration)} files",
             False),
            ("generators", f"neXtBSW {self.version}, sha256 {self.generators_sha256[:SHORT_DIGITS]}, "
                           f"{len(self.generators)} files", False),
        ]

    def lines(self) -> list[str]:
        """The values for a reader (nxconfig identity)."""
        return [f"identity:      {self.identity}",
                f"short:         {self.short} (console: version)",
                f"configuration: {self.configuration_sha256} ({len(self.configuration)} files)",
                f"generators:    {self.generators_sha256} ({len(self.generators)} files, neXtBSW {self.version})"]

    def fields_header(self) -> str:
        """identity/nxIdentityFields.h: the generated entries of the console identification (PLAT-98 extension
        point, ecu/consoleCommands identity/Identity.h) and the identity for the diagnosis (ecu/uds)."""
        rows = " \\\n".join(f'    {{"{name}", "{c_text(value)}", {"true" if line else "false"}}},'
                            for name, value, line in self.fields())
        return ("// Generated by tools/nxconfig/identity.py (through tools/bswgen) - do not edit.\n"
                "// PLAT-79: configuration and generator identity of this image. Same inputs, same file: no time,\n"
                "// no path, no git state (tools/nxconfig/identity.py). `nxconfig identity <project>` prints it.\n"
                "#pragma once\n"
                "\n"
                "// SHA-256 over the configuration and generator sums, 64 hex digits (DID 0xF1F1, configuration.identity)\n"
                f'#define NX_IDENTITY_CONFIGURATION "{self.identity}"\n'
                "\n"
                "// Entries of the console identification: {name, text, also in the line of \"version\"}\n"
                "#define NX_IDENTITY_GENERATED_FIELDS \\\n"
                f"{rows}\n")

    def manifest(self, generator: str, files: dict[str, str]) -> str:
        """<generator>.sha256: identity of the run and the SHA-256 of every file it wrote (sha256sum format below
        the comment lines; `sha256sum -c` checks a generated folder against it)."""
        listing = "".join(f"{hashlib.sha256(files[name].encode('utf-8')).hexdigest()}  {name}\n"
                          for name in sorted(files))
        return (f"# Generated by tools/{generator} - do not edit. PLAT-79: what made the files below.\n"
                f"# generator {generator}, neXtBSW {self.version}\n"
                f"# identity {self.identity}\n"
                f"# configuration {self.configuration_sha256}\n"
                f"# generators {self.generators_sha256}\n"
                f"{listing}")


def c_text(text: str) -> str:
    """Printable ASCII for a C string literal (the values are hex digits and plain words)."""
    return "".join(ch if 0x20 <= ord(ch) < 0x7F and ch not in '"\\' else "_" for ch in text)


def file_sha256(file: Path) -> str:
    """SHA-256 of a file; a text file (no NUL byte) with LF line ends, so a CRLF checkout gives the same sum."""
    data = Path(file).read_bytes()
    if b"\0" not in data:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def digest(inputs) -> str:
    listing = "".join(f"{i.sha256}  {i.label}\n" for i in sorted(inputs, key=lambda i: i.label))
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()


def _below(file: Path, folder: Path) -> str | None:
    try:
        return Path(file).resolve().relative_to(Path(folder).resolve()).as_posix()
    except ValueError:
        return None


def label_of(file: Path, project_dir: Path, tree: Path) -> str:
    """Label of a configuration file: independent of where project and tree lie on this machine."""
    rel = _below(file, project_dir)
    if rel is not None:
        return f"project/{rel}"
    rel = _below(file, tree)
    if rel is not None:
        return f"tree/{rel}"
    return Path(os.path.relpath(Path(file).resolve(), Path(project_dir).resolve())).as_posix()


def configuration_files(config: "nxcfg.Config") -> list[Path]:
    """Every configuration file of the project the generators read (existing files only, no duplicates)."""
    files: list[Path] = [config.project_file, config.board_file, config.derivative_file]
    files += nxcfg.referenced_derivative_files(config)
    rte, diag = config.rte_file, config.diag_file
    if rte is not None:
        files.append(rte)
    if diag is not None:
        files.append(diag)
    files.append(Path(config.running_tree) / TASKS_MANIFEST)
    used = getattr(config.integration, "modules", None) or {}
    files += [config.modules.files[m] for m in sorted(used) if m in config.modules.files]
    files += list(config.code_inputs)
    if config.workspace is not None:
        files.append(config.workspace.file)
    if rte is not None and config.rte is not None:
        base = Path(rte).parent                 # format 2: paths relative to the ECU description (RTE-22)
        if config.rte.can is not None:
            files.append(base / config.rte.can.dbc)
        for component in config.rte.components:
            folder = base / component.path
            files += [folder / "swc.yaml", folder / "generated" / "model.sha256",
                      folder / f"{component.name}_data.m"]
            files += sorted((folder / "generated" / "arxml").glob("*.arxml"))
        dataset = config.project_file.parent / CALIBRATION_DIR
        if dataset.is_dir():
            files += sorted(f for f in dataset.rglob("*") if f.is_file())
    unique: dict[Path, None] = {}
    for f in files:
        f = Path(f).resolve()
        if f.is_file():
            unique.setdefault(f, None)
    return list(unique)


def generator_files(tree: Path = nxcfg.REPO) -> list[Path]:
    files: list[Path] = []
    for folder, patterns, recursive in GENERATOR_SOURCES:
        base = Path(tree) / folder
        for pattern in patterns:
            for f in (base.rglob(pattern) if recursive else base.glob(pattern)):
                rel = f.relative_to(tree).parts
                if f.is_file() and not f.name.startswith("test_") and "test" not in rel and "__pycache__" not in rel:
                    files.append(f)
    version = Path(tree) / VERSION_FILE
    if version.is_file():
        files.append(version)
    return sorted(set(files))


def of(config: "nxcfg.Config", tree: Path = nxcfg.REPO) -> Identity:
    """The identity of a checked configuration; `tree` is the tree the generators run from."""
    project_dir = config.project_file.parent
    configuration = tuple(Input(label_of(f, project_dir, config.running_tree), f, file_sha256(f))
                          for f in configuration_files(config))
    generators = tuple(Input(f"tree/{Path(f).relative_to(tree).as_posix()}", f, file_sha256(f))
                       for f in generator_files(tree))
    try:
        version = read_version(tree)
    except VersionError:
        version = "?"
    return Identity(version, configuration, generators)


def manifest_path(generator: str) -> str:
    return f"{MANIFEST_DIR}/{generator}.sha256"
