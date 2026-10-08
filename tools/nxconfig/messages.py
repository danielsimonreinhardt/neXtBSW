"""Findings of loading and checking a configuration, with file, YAML path and line."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Path_ = tuple[str | int, ...]


def format_path(path: Path_) -> str:
    """('pwm', 0, 'pairs', 1, 'name') -> 'pwm[0].pairs[1].name'"""
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


@dataclass(frozen=True)
class Message:
    severity: Literal["error", "warning"]
    text: str
    file: Path | None = None
    path: Path_ = ()
    line: int | None = None                         # 1-based, None if unknown

    @property
    def yaml_path(self) -> str:
        return format_path(self.path)

    def __str__(self) -> str:
        where = self.file.name if self.file else ""
        if self.line:
            where += f":{self.line}"
        if self.path:
            where += f" ({self.yaml_path})" if where else self.yaml_path
        return f"{where}: {self.severity}: {self.text}" if where else f"{self.severity}: {self.text}"


class ConfigErrors(Exception):
    """Raised by load() if at least one error was found; carries all messages."""

    def __init__(self, messages: list[Message]):
        self.messages = messages
        super().__init__("\n".join(str(m) for m in messages if m.severity == "error"))
