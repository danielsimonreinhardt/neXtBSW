# tools/schedana – analyse processor load, scheduling and data flow (TOOL-09)

Answers three questions about an ECU configuration:

1. **Does the schedule fit?** Utilisation per task, response time with fixed priorities (all tasks including BSW, ISR
   groups as interferers), deadline violations, load peaks over the hyperperiod, suggestions for the offset of
   runnables.
2. **Are the priorities sensible?** A warning with a reason if an application task lies above a safety- or
   communication-relevant BSW task. No lock – the choice stays free (user decision 2026-09-15).
3. **Is the data flow right?** Reader before writer in the same cycle, age and consistency of values across tasks,
   rate transitions, signals without writer or reader, cycles (control loops).

The core is **without Qt** and without hardware access (architecture section 10, review question 8). Report as text
and as JSON; every message names task/runnable/signal and the YAML path, so that a user interface can attach it to
the line that caused it (TOOL-10).

## Invocations

```sh
# configuration only (structure, priorities, data flow; run times are missing)
.venv/Scripts/python tools/schedana analyse projects/evalboard_s32k144_demo

# with measured values from the target and assumptions for BSW tasks/ISR groups
.venv/Scripts/python tools/schedana analyse projects/evalboard_s32k144_demo \
    --measure console.txt --budget tools/schedana/test/budget_demo.yaml --json bericht.json

# constructed schedule without a project (test profile, estimate, what-if)
.venv/Scripts/python tools/schedana analyse --scenario tools/schedana/test/scenarios/overloaded.yaml

# only read in the console output (also from standard input: --input -)
.venv/Scripts/python tools/schedana measure --input console.txt --json

# look at the model or export it as a scenario (input for TOOL-10, for what-if)
.venv/Scripts/python tools/schedana show projects/evalboard_s32k144_demo
.venv/Scripts/python tools/schedana scenario projects/evalboard_s32k144_demo --measure console.txt -o modell.yaml
```

Return value: 0 without findings, 1 from one error on (`--fail-on warning|error|none`), 2 on faulty input.

## Where the values come from

| Value | Source |
|---|---|
| application tasks, runnables, offset, order, connections | `projects/<project>/rte.yaml` (via `tools/rtegen`, read only) |
| BSW tasks, priorities, OS tick, ISR groups | `ecu/asyncCoreConfiguration/tasks.yaml` |
| board/derivative (signal names of the connections) | `tools/nxconfig` |
| run time and jitter per runnable, cycles and overruns per task | console `rte json` (new) or `rte runnables` |
| CPU load, runs, mean/minimum/maximum per task and ISR group | console `stats cpu` (OpenBSW RuntimeMonitor) |
| stack size and usage per task | console `stats stack` |
| period and run time of the BSW tasks/ISR groups without measurement, length of interrupt locks, deviating deadlines | budget file (`--budget`, example `test/budget_demo.yaml`) |
| roles (safety, communication) for the priority warning | `roles.yaml`, replaceable with `--roles` |

**Periods from a measurement (TOOL-34).** A configured period is never changed by a measurement; the measurement only brings
runtime and jitter (a remark names a run count that does not fit). A BSW task or ISR group without a configured period gets
its period from the runs in the window, put on the OS tick grid: the **nearest** tick multiple if the deviation is within the
measurement uncertainty (run count ±1, at least 1 %: 100 runs in 999 995 µs → 10 ms), otherwise (sporadic ISR, event-driven
task) rounded **down** so its disturbance is not underestimated; a rate faster than one tick keeps its measured value.

**Activation limit.** The simulation over the hyperperiod handles at most `MAX_JOBS` (40 000) activations; above it the list
stops at a release instant and the report sets `hyperperiod.truncated`, `analysed_ms`, `activations`,
`activations_analysed` and the finding `hyperperiod_truncated`.

`schedana` opens **no** serial port: the console output comes as a file or via standard input (`--measure -`); recording is
the caller's job (`python tools/studio console --port COM9 > console.txt` or the Studio). The parser works line by line;
lost or garbled lines (IO-17) cost only that line and are counted in the report. `rte json` states the number of tasks and
runnables in its header and closes with an end marker, which is how missing lines are detected. Echo of the command, prompt,
the acknowledgement ` ok`, OpenBSW logger lines and the `### <command>` headings and `# …` comments of a capture recorded
with `tools/boardtest --record` are expected noise, not unreadable (TOOL-15).

## Assumptions of the response time analysis

A1 Fixed priorities, preemptive, one core; every task has its own priority (OpenBSW: context number).
A2 ISR groups lie above all tasks, modelled as periodic interferers with their shortest interval; nesting within a
   group is not modelled.
A3 An application task is calculated with its **longest cycle** (the tick of the hyperperiod with the most runnables
   due, offset taken into account). This is an upper bound for all cycles and pessimistic for multi-rate tasks; the
   simulation over the hyperperiod shows the actual cycles.
A4 Blocking = `blocking_us` of the task plus `max_lock_us` of the system, once per activation (interrupt locks of the
   BSW, IO-14/IO-16). Priority inheritance of mutexes is not modelled.
A5 Deadline = period, unless `deadline_ms` says otherwise.
A6 Run times are measured maxima or estimates, **not a proven WCET**.
A7 All tasks start together (`RteSystem::run()` starts every task with `scheduleAtFixedRate`), so the critical instant
   really occurs. A phase per task cannot be configured today, only the offset of a runnable within its task.
A8 Only for systems with mode switch runnables (RTE-08, RTE-17): they are event driven load of their task. The worst
   switch of a task – every runnable one change of the ECU mode runs: all exits of the old mode, the transitions old
   → new, all entries of the new mode; in the first cycle also the entry of the initial mode STARTUP – lies in the
   same cycle as its longest periodic cycle. A higher priority task disturbs with one switch per busy window (the ECU
   mode changes rarely compared to the task periods). Mode switch runnables have no period: they are not part of the
   long term utilisation and not of the simulation over the hyperperiod. Any pair of modes is considered, because a
   task sees a mode change only at its cycle start and skips a mode shorter than its cycle.

### Mode switch runnables (RTE-17)

* **Configuration:** `rteload` takes them from `tools/rtegen` (`mode_runnable_table`, the rows of `rte::MODE_RUNNABLES`, one
  per event). Their accesses count in their task at the cycle start, not as init runnables; they have no rate.
* **Scenario:** per application task `mode_runnables: [{name, event: entry|exit|transition, mode, from, wcet_us, mean_us,
  min_us, reads, writes}]` (`from` only for a transition; test profile `test/scenarios/modes.yaml`).
* **Measurement:** `rte runnables` prints `mode <Swc.Runnable> (task <task>): on entry RUN, calls 3, last 12 us, max 15 us`;
  `rte json` one object `{"t":"mode", ...}` per event and `"modes":<n>` in the header. A runnable with `calls 0` gets no
  runtime (remark); switch the ECU mode while measuring.
* **Report:** `mode_switches` (JSON), the section "Moduswechsel je Task", `mode_switch_us` in `utilisation` and
  `response_times`, findings `mode_switch_load` and `mode_runtime_missing`, assumption A8. A system without such runnables
  gets exactly the report it got before.

### Implicit access (RTE-09)

* **Configuration:** a runnable with `DATA-READ-ACCESSS`/`DATA-WRITE-ACCESSS` works on copies of its own (`plan_implicit`).
  `rteload` marks these accesses (`Access.implicit`) and gives each runnable the RAM of its copies (`implicit_bytes`) and the
  lock sections of its wrapper (`implicit_locks`; none for copies of task copies).
* **Data flow:** order and age are unchanged; an implicit reader of an `atomic` signal keeps one value for its whole run
  (finding `implicit_hand_over`, info, instead of `atomic_hand_over`, warning).
* **Report:** `implicit_access` (JSON) and the section "Implizite Zugriffe (Kopie je Runnable)". The time of the copies is
  part of the measured runtime. Scenario files do not describe implicit access yet.

## Measuring on the board and overrun test

1. Open the console, `rte reset`, wait (the CPU statistics cover the last second, the RTE statistics everything since `rte
   reset`), send `rte json`, `rte runnables`, `stats cpu` and `stats stack` and record them into a file.
2. `schedana analyse <project> --measure <file> --budget <file>`; the finding `load_measured_vs_analysed` compares the
   measured CPU load (100 % − idle) with the analysed upper bound.
3. **Overrun test:** the switch `NX_RTE_LOAD_TEST_US` (length of the busy loop) with `NX_RTE_LOAD_TEST_INDEX` (runnable,
   default 0) in `ecu/application/src/systems/RteSystem.cpp`; without it no code is generated, with it `rte runnables` and
   the header of `rte json` say so and the report warns (`load_test_build`). For the demo project:
   `NX_RTE_LOAD_TEST_US=12000`, `NX_RTE_LOAD_TEST_INDEX=1` (`DemoSwc.Run100ms`): every tenth cycle of the 10 ms task
   exceeds its period, the board stays operable.

## Package structure

| File | Content |
|---|---|
| `model.py` | data model (tasks, runnables, ISR groups, signals, accesses) without reference to any tool |
| `scenario.py` | self-contained analysis input (test profile, export), role table |
| `rteload.py` | model from a project (`tools/nxconfig` + `tools/rtegen`, read only), budget file |
| `measure.py` | parser for `rte json`, `rte runnables`, `stats cpu`, `stats stack`; measured values into the model |
| `scheduling.py` | utilisation, response time analysis, hyperperiod with simulation, offset suggestions |
| `priorities.py` | priority order and warning with a reason |
| `dataflow.py` | data flow analysis and cycle search |
| `report.py` | report (text and JSON), check of the measured values against the analysis |
| `findings.py` | finding as a data object (severity, code, message, target, YAML path) |
| `cli.py`, `__main__.py` | command line |
| `roles.yaml` | default roles per task/ISR group name (data, replaceable) |
| `test/scenarios`, `test/console`, `test/budget_demo.yaml` | test profiles and example measurements |
| `test_schedana.py` | host tests (`python -m unittest tools/schedana/test_schedana.py`) |

## Connection to the Studio (TOOL-10)

`tools/studio` must not change anything here; the connection is prepared:

```python
from schedana import rteload, measure, analyse
model = rteload.load(project.file)               # the same project model as studio.core.project
values = measure.parse(text, known_tasks=[t.name for t in model.tasks])
remarks = measure.apply_to(model, values)
report = analyse(model, values, remarks)         # report.to_dict() / to_text()
```

* `report.to_dict()` is the complete, JSON-capable view (`hyperperiod.windows`, `hyperperiod.simulation`, `utilisation`,
  `response_times`), the basis of the timeline in TOOL-10.
* Every finding has `severity`, `code`, `message`, `target` and `where` (YAML path such as `tasks[1].runnables[0]`);
  `reader_before_writer` names the entry of the **reading** runnable, the port is in `detail.port`.
* `scheduling.simulate(jobs, horizon_us, trace=True)` additionally returns `pieces`, every execution slice in time order
  (the Studio timeline draws them); the aggregates are the same as without `trace`.
* `runtime_missing` names every task/ISR group once (`can (task and ISR group)`); `utilisation.without_runtime_by_kind`
  keeps tasks and ISR groups apart. Recording the console belongs in the Studio, reading it in belongs here.
* `scenario.to_dict(model)` / `from_dict` allow what-if calculations without writing `rte.yaml`.

## Open / suggestions to other tools

* The **role** of a task (safety, communication) belongs in the configuration. `tasks.yaml` and `rte.yaml` do not
  allow an additional key today (rtegen rejects unknown keys), so the default is in `roles.yaml`. Suggestion:
  optional `roles: [...]` per task in `tasks.yaml`/`rte.yaml` (rtegen, ARCH-05).
* **Deadline and activation period of the BSW tasks** are nowhere in the configuration; today they come from the
  measurement (runs in the measurement window) or from the budget file.
* Event-triggered runnables other than the mode switch runnables (A8), queues between tasks and multi-core are not
  modelled (as in RTE-02).
* The analysis knows no priority inheritance; the mutex variant from IO-16 is represented as blocking time.
