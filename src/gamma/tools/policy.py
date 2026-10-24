from dataclasses import dataclass

@dataclass(frozen=True)
class ToolGrants:
    enabled: bool
    use_tools: bool
    read_memory: bool
    write_memory: bool
    is_owner: bool


def allowed(name: str, grants: ToolGrants) -> bool:
    if not grants.enabled or not grants.use_tools:
        return False
    if name in ("provider_status", "recent_artifacts"):
        return True
    if name in ("search_memory", "known_people", "memory_stats"):
        return grants.read_memory
    if name == "save_memory":
        return grants.write_memory
    if name == "save_core_memory":
        return grants.write_memory and grants.is_owner
    return False
