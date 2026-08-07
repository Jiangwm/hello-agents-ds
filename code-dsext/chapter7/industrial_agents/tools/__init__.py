from .industrial_tools import IndustrialTool, ToolParameter, ToolRegistry, builtin_tools


def create_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in builtin_tools():
        registry.register(tool)
    return registry


__all__ = [
    "IndustrialTool",
    "ToolParameter",
    "ToolRegistry",
    "create_default_tool_registry",
]
