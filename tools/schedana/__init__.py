"""schedana - analysis of processor load, scheduling and data flow of the neXtBSW RTE (TOOL-09).

Core without Qt (architecture section 10, review question 8): everything here is plain Python data, so the
command line (cli.py), the Studio (tools/studio) and a later web frontend use the same logic.

    from schedana import analyse, measure, rteload
    model = rteload.load("projects/evalboard_s32k144_demo")          # configuration, read only
    values = measure.parse(open("console.txt").read())               # rte json / rte runnables / stats cpu
    measure.apply_to(model, values)
    report = analyse(model, values)
    print(report.to_text()); print(report.to_json())
"""

from .findings import ERROR, INFO, WARNING, Finding
from .model import APPLICATION, BSW, IsrSpec, ModelError, RunnableSpec, SignalSpec, SystemModel, TaskSpec
from .report import Report, analyse

__all__ = ["analyse", "Report", "Finding", "ERROR", "WARNING", "INFO", "SystemModel", "TaskSpec",
           "RunnableSpec", "IsrSpec", "SignalSpec", "ModelError", "BSW", "APPLICATION"]
