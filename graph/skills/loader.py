"""Auto-discovers @tool-decorated functions from a LangChain tools package."""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from langchain_core.tools import BaseTool


class SkillLoader:
    """Discovers and registers every BaseTool found in a tools package.

    New tools dropped into graph/tools/ are picked up automatically on the
    next instantiation — no __init__.py edits required.
    """

    def __init__(self, tools_package: str = "graph.tools") -> None:
        self._tools: dict[str, BaseTool] = {}
        self._tests: dict[str, list[dict]] = {}
        self._load(tools_package)

    def _load(self, package: str) -> None:
        pkg = importlib.import_module(package)
        for _, module_name, _ in pkgutil.iter_modules(pkg.__path__):
            mod = importlib.import_module(f"{package}.{module_name}")
            for attr in dir(mod):
                obj = getattr(mod, attr)
                if isinstance(obj, BaseTool):
                    self._tools[obj.name] = obj
            if hasattr(mod, "SKILL_TESTS"):
                for entry in mod.SKILL_TESTS:
                    tool_name = entry.get("tool")
                    if tool_name:
                        self._tests.setdefault(tool_name, []).append(entry)

    def get(self, name: str) -> BaseTool:
        return self._tools[name]

    def all(self) -> list[BaseTool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def invoke(self, name: str, inputs: dict) -> Any:
        return self._tools[name].invoke(inputs)

    def tests(self, name: str | None = None) -> list[dict]:
        if name is not None:
            return self._tests.get(name, [])
        return [t for cases in self._tests.values() for t in cases]


_loader: SkillLoader | None = None


def get_loader() -> SkillLoader:
    global _loader
    if _loader is None:
        _loader = SkillLoader()
    return _loader
