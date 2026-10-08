"""Findings of the analyses: one data object per message, sortable and machine readable.

Every finding carries the place that caused it (where: YAML path like tasks[1].runnables[0], target: name of the
task, runnable or signal) so that a frontend can put the message next to the line that caused it (TOOL-10).
"""

from dataclasses import dataclass, field

ERROR = "error"
WARNING = "warning"
INFO = "info"

SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass
class Finding:
    severity: str
    code: str          # stable identifier, e.g. "deadline_missed"
    message: str       # one sentence, what and why
    target: str = ""   # task, runnable or signal the finding is about
    where: str = ""    # YAML path in rte.yaml / the scenario
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message,
                "target": self.target, "where": self.where, "detail": self.detail}

    def __str__(self) -> str:
        place = f" [{self.where}]" if self.where else ""
        return f"{self.severity}: {self.message}{place}"


def sort_findings(findings: list) -> list:
    return sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.code, f.target))


def counts(findings: list) -> dict:
    result = {ERROR: 0, WARNING: 0, INFO: 0}
    for f in findings:
        result[f.severity] = result.get(f.severity, 0) + 1
    return result
