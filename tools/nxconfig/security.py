# SPDX-License-Identifier: Apache-2.0
"""NVM-14: keys of the hardware security module (project.yaml `security`, s32k1xx: CSEc).

Checks, each with its YAML path:
- WRITE_PROTECTION is refused in every spelling and every place of the section. The schema (model.SecurityKey)
  already does not offer it; this second check guards against a later extension of the schema. It is an error on
  development boards and - until the user decides otherwise - on every other stage as well.
- stage `production` is refused: per device keys are not designed yet.
- key names are SHE names and fit the key space `nvm.partition.key_slots`; `verify_only` needs
  `nvm.partition.key_verify_only`.
- `master_ecu_key` is a name of the key store, never key material.

The key itself lives in the key store of the workstation (keystore.py); master_ecu_key_name() is how a tool finds
its name.
"""

from __future__ import annotations

import re

from .config import Config, Reporter
from .keystore import HEX_RUN_RE
from .model import WRITE_PROTECTION_REFUSED, is_write_protection

SECURITY = "security"
SYSTEM_KEYS = ("MASTER_ECU_KEY", "BOOT_MAC_KEY", "BOOT_MAC")
USER_KEY_RE = re.compile(r"^KEY_([1-9][0-9]*)$")


def master_ecu_key_name(cfg: Config) -> str:
    """Name of the MASTER_ECU_KEY in the key store: security.master_ecu_key, else the board name (one key for every
    project of a board, like the bootloader master key of BL-03)."""
    use = cfg.project.security
    return (use.master_ecu_key if use is not None and use.master_ecu_key else None) or cfg.board.board.name


def _walk(node, path=()):
    """(path, mapping key or scalar) of a YAML tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield path + (k,), k
            yield from _walk(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, path + (i,))
    else:
        yield path, node


def write_protection_findings(tree) -> list[tuple[tuple, str]]:
    """Every place of the security section of a project YAML tree that asks for WRITE_PROTECTION."""
    if not isinstance(tree, dict) or SECURITY not in tree:
        return []
    return [(path, str(value)) for path, value in _walk(tree[SECURITY], (SECURITY,)) if is_write_protection(value)]


def check(cfg: Config, rep: Reporter) -> None:
    use = cfg.project.security
    source = rep.sources.get("project")
    stage = use.stage if use is not None else "development"
    board = "development boards" if stage == "development" else f"stage {stage} as well"
    # 1. the guard: raw YAML first (catches fields a later schema might add), then the loaded flags
    reported = set()
    for path, _ in write_protection_findings(source.tree if source is not None else None):
        rep.error("project", path, f"{WRITE_PROTECTION_REFUSED}; an error on {board}")
        reported.add(path[:3])
    if use is None:
        return
    for name, key in use.keys.items():
        where = (SECURITY, "keys", name, "flags")
        if any(is_write_protection(f) for f in key.flags) and where[:3] not in reported:
            rep.error("project", where, f"{WRITE_PROTECTION_REFUSED}; an error on {board}")
    if stage != "development":
        rep.error("project", (SECURITY, "stage"), f"stage {stage}: per device keys are not offered yet - only "
                                                  f"development boards with one MASTER_ECU_KEY from the key store "
                                                  f"(NVM-14)")
    if use.master_ecu_key and HEX_RUN_RE.search(use.master_ecu_key):
        rep.error("project", (SECURITY, "master_ecu_key"), "looks like key material - name the key of the key store "
                                                           "here, never write the key into the project")
    _key_names(cfg, rep)


def _key_names(cfg: Config, rep: Reporter) -> None:
    use = cfg.project.security
    if not use.keys:
        return
    nvm = cfg.project.nvm
    partition = nvm.partition if nvm is not None else None
    slots = partition.key_slots if partition is not None else None
    if not slots:
        rep.error("project", (SECURITY, "keys"), "keys need a key space: nvm.partition.key_slots reserves none")
        return
    emulation = cfg.derivative.eeprom_emulation
    entry = next((k for k in (emulation.keys if emulation is not None else []) if k.slots == slots), None)
    free = slots - entry.system_slots if entry is not None else None     # None: nvm.py reported the key space
    for name, key in use.keys.items():
        where = (SECURITY, "keys", name)
        match = USER_KEY_RE.match(name)
        if name not in SYSTEM_KEYS and match is None:
            rep.error("project", where, f"unknown key {name}: SHE names {', '.join(SYSTEM_KEYS)} or KEY_1 ... KEY_<n>")
        elif match is not None and free is not None and int(match.group(1)) > free:
            rep.error("project", where, f"{name} lies beyond the key space: {slots} slots leave KEY_1 ... "
                                        f"KEY_{free} (nvm.partition.key_slots)")
        if "verify_only" in key.flags and not partition.key_verify_only:
            rep.error("project", where + ("flags",), "verify_only needs the security flag extension "
                                                     "(nvm.partition.key_verify_only)")
