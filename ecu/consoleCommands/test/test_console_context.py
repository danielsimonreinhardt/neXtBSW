"""IO-42: no console command prints in a task that carries the deadman poll (general safeguard).

Board finding 2026-09-21 (session d3): "kl30b status" triggered the halt deadman of the debug halt guard every time
(4 of 4), then KL30B and DRV_EN were off. The command was registered with the context of its system
(AsyncCommandWrapper(command, TASK_BSP)), so it formatted and wrote its ~340 characters to the UART synchronously
from TASK_BSP - about 30 ms at 115200 Bd (86.8 us per character). The deadman poll runs in TASK_BSP every 1 ms and
the deadman fires after 10 ms (LpitHaltDeadman::TIMEOUT_MS). "gd status" did not trigger it: SYS-13 had moved it
into the console context.

The rule this test enforces over the whole repository (without external/): every console command is registered
with the console context (::console::CONSOLE_CONTEXT, TASK_IDLE) and touches its system only through
::console::runInOwnerContext() - so the task of the system spends only the snapshot on a command, never the output.
The output of a command in the console context costs no other task anything: TASK_IDLE is the lowest context.
A command outside the console context is only allowed with an entry in ALLOWED below, which names the context and
the reason why its output cannot hold a task with a deadman poll for longer than the budget. An entry that no
longer matches anything is an error as well (a stale exception hides nothing, but it misleads the reader).

The timing of the commands themselves is measured by the host tests of the console context
(libs/bsw/consoleContext/test/host/*ConsoleHostTest.cpp, libs/bsw/highSideSwitch/test/host/
HighSideSwitchConsoleHostTest.cpp): the owner waits for one snapshot, the deadman poll gap stays far below 10 ms.

Run: .venv/Scripts/python -m unittest ecu/consoleCommands/test/test_console_context.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

CONSOLE_CONTEXTS = {
    "::console::CONSOLE_CONTEXT",
    "console::CONSOLE_CONTEXT",
    "CONSOLE_CONTEXT",
    # the same context spelled out (the logger command of the application, SYS-15)
    "::async::AsyncBinding::AdapterType::TASK_IDLE",
}

# (file relative to the repository, wrapper name) -> (context as written, reason)
ALLOWED = {
    ("platforms/s32k1xx/port/main/src/systems/BspSystem.cpp", "_asyncHardFaultTestCommand"): (
        "context",
        "test build only (NX_HARDFAULT_TEST_COMMAND): faults on purpose in TASK_BSP (board test PLAT-20); one line "
        "of at most ~40 characters (3.5 ms) and then the fault handler resets",
    ),
    ("ecu/application/src/systems/SafetySystem.cpp", "_asyncCommandWrapperForSafetyCommand"): (
        "context",
        "TASK_SAFETY: 'safety dst/est/prt' change the pin configuration the safety task supervises and log through "
        "the buffered logger (no UART in the task); 'mpu'/'wdg' print one line (< 70 characters, 6 ms) and then "
        "force a fault resp. a watchdog reset on purpose",
    ),
    ("ecu/application/src/systems/RuntimeSystem.cpp", "_asyncCommandWrapperForStatisticsCommand"): (
        "context",
        "TASK_BACKGROUND (context 1) is below TASK_BSP (context 2): its output cannot hold the deadman poll",
    ),
}

SOURCE_GLOBS = ("libs", "platforms", "ecu", "bootloader")
SKIP_PARTS = {"test", "external", "build"}

MEMBER_DECL = re.compile(r"::console::AsyncCommandWrapper\s+(\w+)\s*;")
GLOBAL_DEF = re.compile(r"::console::AsyncCommandWrapper\s*\n?\s*(\w+)\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)\s*;", re.S)


def _sources():
    for top in SOURCE_GLOBS:
        base = REPO / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix not in (".cpp", ".h", ".hpp"):
                continue
            rel = path.relative_to(REPO)
            if SKIP_PARTS.intersection(rel.parts):
                continue
            yield rel, path.read_text(encoding="utf-8", errors="replace")


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def find_wrappers(sources):
    """Every AsyncCommandWrapper of the given sources: [(file, name, context)]; unresolved ones with context None."""
    sources = [(rel, _strip_comments(text)) for rel, text in sources]
    members = set()
    for _, text in sources:
        members.update(MEMBER_DECL.findall(text))
    found = []
    for rel, text in sources:
        for name, _command, context in GLOBAL_DEF.findall(text):
            found.append((rel.as_posix(), name, " ".join(context.split())))
        for name in members:
            # member initializer "name(command, context)" in a constructor's initializer list
            for m in re.finditer(r"[,:]\s*" + re.escape(name) + r"\s*\(\s*([^,()]+?)\s*,\s*([^()]+?)\s*\)", text):
                found.append((rel.as_posix(), name, " ".join(m.group(2).split())))
    declared = {name for name in members}
    initialized = {name for _, name, _ in found}
    for name in sorted(declared - initialized):
        found.append(("?", name, None))
    return found


def check(found, allowed=ALLOWED):
    """Messages for every wrapper outside the console context without an entry in ALLOWED, and for stale entries."""
    errors = []
    used = set()
    for rel, name, context in found:
        if context is None:
            errors.append(f"{name}: AsyncCommandWrapper whose context this check cannot find (declared, never "
                          f"initialized as name(command, context))")
            continue
        if context in CONSOLE_CONTEXTS:
            continue
        key = (rel, name)
        entry = allowed.get(key)
        if entry is None or entry[0] != context:
            errors.append(
                f"{rel}: console command {name} runs in '{context}', not in ::console::CONSOLE_CONTEXT - its output "
                f"holds that task at the UART (86.8 us per character; IO-42: 'kl30b status' held TASK_BSP ~30 ms and "
                f"the 10 ms deadman fired). Register it with ::console::CONSOLE_CONTEXT and take the state through "
                f"::console::runInOwnerContext(), or add a reasoned entry to ALLOWED")
            continue
        used.add(key)
    for key in sorted(set(allowed) - used):
        errors.append(f"{key[0]}: ALLOWED entry {key[1]} matches no console command any more")
    return errors


class ConsoleContextRuleTest(unittest.TestCase):
    def test_repository_follows_the_rule(self):
        found = find_wrappers(_sources())
        # the check must see the known users, otherwise the patterns have gone blind
        names = {name for _, name, _ in found}
        for expected in ("_asyncCommand", "_asyncCommandWrapperForSafetyCommand", "asyncCommandWrapper"):
            self.assertIn(expected, names)
        self.assertGreaterEqual(len(found), 12)
        errors = check(found)
        self.assertEqual(errors, [], "\n".join(errors))

    def test_high_side_switch_is_in_the_console_context(self):
        """The board finding itself: the command of the high-side switch runs in the console context."""
        found = find_wrappers(_sources())
        hss = [f for f in found if f[0].endswith("libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitchSystem.cpp")]
        self.assertEqual(len(hss), 1)
        self.assertIn(hss[0][2], CONSOLE_CONTEXTS)

    def test_rule_catches_the_old_high_side_switch(self):
        """Deliberately inserted fault: the high-side switch as it was before IO-42 (command in its own context)."""
        header = "class S { ::console::AsyncCommandWrapper _asyncCommand; };"
        old = "S::S(ContextType const context) : _command(*this), _asyncCommand(_command, context) {}"
        found = find_wrappers([(Path("libs/bsw/x/S.h"), header), (Path("libs/bsw/x/S.cpp"), old)])
        errors = check(found, allowed={})
        self.assertEqual(len(errors), 1)
        self.assertIn("runs in 'context'", errors[0])
        new = "S::S(ContextType const context) : _command(*this), _asyncCommand(_command, ::console::CONSOLE_CONTEXT) {}"
        self.assertEqual(check(find_wrappers([(Path("libs/bsw/x/S.h"), header), (Path("libs/bsw/x/S.cpp"), new)]),
                               allowed={}), [])

    def test_stale_and_unresolved_entries_are_errors(self):
        errors = check([], allowed={("a.cpp", "_gone"): ("context", "reason")})
        self.assertEqual(len(errors), 1)
        self.assertIn("matches no console command", errors[0])
        header = "class S { ::console::AsyncCommandWrapper _hidden; };"
        errors = check(find_wrappers([(Path("libs/bsw/x/S.h"), header)]), allowed={})
        self.assertEqual(len(errors), 1)
        self.assertIn("cannot find", errors[0])

    def test_comments_do_not_count(self):
        header = "class S { ::console::AsyncCommandWrapper _asyncCommand; };"
        text = ("// _asyncCommand(_command, context)\n/* , _asyncCommand(_command, TASK_BSP) */\n"
                "S::S() : _asyncCommand(_command, ::console::CONSOLE_CONTEXT) {}")
        found = find_wrappers([(Path("libs/bsw/x/S.h"), header), (Path("libs/bsw/x/S.cpp"), text)])
        self.assertEqual(check(found, allowed={}), [])
        self.assertEqual(len(found), 1)


if __name__ == "__main__":
    unittest.main()
