from __future__ import annotations

from .base import Tool
from .builtin import (
    KnownPeopleTool,
    MemoryStatsTool,
    ProviderStatusTool,
    RecentArtifactsTool,
    SaveCoreMemoryTool,
    SaveMemoryTool,
    SearchMemoryTool,
)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._register_defaults()

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def tool_summaries(self) -> list[str]:
        return [f"{tool.name}: {tool.description}" for tool in sorted(self._tools.values(), key=lambda item: item.name)]

    def _register_defaults(self) -> None:
        for tool in (
            MemoryStatsTool(),
            KnownPeopleTool(),
            ProviderStatusTool(),
            RecentArtifactsTool(),
            SearchMemoryTool(),
            SaveMemoryTool(),
            SaveCoreMemoryTool(),
        ):
            self.register(tool)
