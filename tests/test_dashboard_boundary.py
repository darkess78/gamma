from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "src" / "gamma" / "dashboard"
FORBIDDEN_MODULES = {
    "gamma.conversation.service",
    "gamma.memory.service",
    "gamma.persona.emotion_service",
    "gamma.system.status",
    "gamma.voice.roundtrip",
    "gamma.integrations.twitch.trust",
}


def _absolute_module(source: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package_parts = ["gamma", "dashboard"]
    keep = max(0, len(package_parts) - node.level + 1)
    prefix = package_parts[:keep]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def test_dashboard_does_not_import_shana_domain_services() -> None:
    violations: list[str] = []
    for source in DASHBOARD_ROOT.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(_absolute_module(source, node))
            for module in modules:
                if module in FORBIDDEN_MODULES:
                    violations.append(f"{source.relative_to(PROJECT_ROOT)} imports {module}")
    assert not violations, "Dashboard/Shana boundary violations:\n" + "\n".join(violations)
