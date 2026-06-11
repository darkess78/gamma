from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolResult:
    """Tool result.
    
    Attributes:
        ok: Success status.
        output: Output text.
        metadata: Metadata dict.
    """
    ok: bool
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Tool:
    """Tool.
    
    Attributes:
        name: Tool name.
        description: Tool description.
    
    Methods:
        run: Run tool.
    """
    name: str = "tool"
    description: str = ""

    def run(self, **kwargs) -> ToolResult:
        """Run tool.
        
        Returns:
            ToolResult: Tool result.
        """
        raise NotImplementedError
