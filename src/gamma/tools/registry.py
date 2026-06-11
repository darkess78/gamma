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
    """Tool registry.
    
    Attributes:
        _tools: Registered tools dict.
    
    Methods:
        __init__: Initialize registry.
        register: Register tool.
        get: Get tool.
        names: Get tool names.
        tool_summaries: Get tool summaries.
        _register_defaults: Register default tools.
    """

    def __init__(self) -> None:
        """Initialize registry.
        
        Registers default tools.
        """
        self._tools: dict[str, Tool] = {}
        self._register_defaults()

    def register(self, tool: Tool) -> None:
        """Register tool.
        
        Args:
            tool: Tool to register.
        """
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Get tool.
        
        Args:
            name: Tool name.
        
        Returns:
            Tool | None: Tool or None.
        """
        return self._tools.get(name)

    def names(self) -> list[str]:
        """Get tool names.
        
        Returns:
            list[str]: Sorted tool names.
        """
        return sorted(self._tools.keys())

    def tool_summaries(self) -> list[str]:
        """Get tool summaries.
        
        Returns:
            list[str]: Tool name and description summaries.
        """
        return [f"{tool.name}: {tool.description}" for tool in sorted(self._tools.values(), key=lambda item: item.name)]

    def _register_defaults(self) -> None:
        """Register default tools.
        
        Registers builtin tools: memory_stats, known_people, provider_status,
        recent_artifacts, search_memory, save_memory, save_core_memory.
        """
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
