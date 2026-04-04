from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ToolResult:
    ok: bool
    output: str
    metadata: dict = field(default_factory=dict)


class Tool:
    name: str = "tool"

    def run(self, **kwargs) -> ToolResult:
        raise NotImplementedError
