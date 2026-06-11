from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolResult:
    ok: bool
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Tool:
    name: str = "tool"
    description: str = ""

    def run(self, **kwargs) -> ToolResult:
        raise NotImplementedError
