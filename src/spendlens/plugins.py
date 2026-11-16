"""Extension points.

Built-in features (spendlens.features.*) and third-party packages use the same API.
A plugin is any object/module with a `register(registry)` function. External plugins
are discovered through the `spendlens.plugins` entry-point group:

    # pyproject.toml of your extension package
    [project.entry-points."spendlens.plugins"]
    acme = "acme_spendlens:plugin"

    # acme_spendlens/__init__.py
    import typer
    from spendlens.plugins import Registry

    def register(reg: Registry) -> None:
        sub = typer.Typer(help="ACME-specific FinOps checks")

        @sub.command()
        def gpu_idle(): ...

        reg.add_typer(sub, "acme")                       # -> `spendlens acme gpu-idle`
        reg.add_mod(Path(__file__).parent / "mod")       # extra Powerpipe controls/benchmarks
        reg.add_doctor_check(lambda cfg: [...])          # extra `spendlens doctor` rows
        reg.add_ai_tool(my_tool)                         # extra tool for `spendlens ask`
        reg.add_estimator("my_control", fn)              # $/month savings for a control's alarms
        reg.add_dashboard_section("gpu", "GPU", render)  # extra dashboard tab

Plugin settings live under `plugins.<name>` in finops.yaml (or --set plugins.acme.x=1)
and are available as `cfg.plugins.get("acme", {})`.

Disable plugins with SPENDLENS_DISABLE_PLUGINS=acme,other (or "*" for all external ones).
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:
    from .config import Config
    from .doctor import Check

ENTRY_POINT_GROUP = "spendlens.plugins"
BUILTIN_FEATURES: tuple[str, ...] = (
    "spendlens.features.tags",
    "spendlens.features.rightsize",
    "spendlens.features.waste",
    "spendlens.features.cost",
    "spendlens.features.anomalies",
    "spendlens.features.forecast",
    "spendlens.features.savings_cmd",
    "spendlens.features.dashboard",
    "spendlens.features.ask",
    "spendlens.features.notify",
    "spendlens.features.report",
    "spendlens.features.history_cmd",
)


@dataclass
class AITool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema
    handler: Callable[..., Any]


@dataclass
class PluginInfo:
    name: str
    origin: str  # builtin | entry-point value
    error: str | None = None


@dataclass
class Registry:
    commands: list[tuple[Callable[..., Any], str | None]] = field(default_factory=list)
    typers: list[tuple[typer.Typer, str]] = field(default_factory=list)
    doctor_checks: list[Callable[[Config], list[Check]]] = field(default_factory=list)
    ai_tools: dict[str, AITool] = field(default_factory=dict)
    mod_paths: list[Path] = field(default_factory=list)
    estimators: dict[str, Callable[..., float | None]] = field(default_factory=dict)
    dashboard_sections: list[tuple[str, str, Callable[..., str]]] = field(default_factory=list)
    plugins: list[PluginInfo] = field(default_factory=list)

    def add_command(self, fn: Callable[..., Any], name: str | None = None) -> None:
        self.commands.append((fn, name))

    def add_typer(self, sub: typer.Typer, name: str) -> None:
        self.typers.append((sub, name))

    def add_doctor_check(self, fn: Callable[[Config], list[Check]]) -> None:
        self.doctor_checks.append(fn)

    def add_ai_tool(self, tool: AITool) -> None:
        if tool.name in self.ai_tools:
            raise ValueError(f"AI tool '{tool.name}' already registered")
        self.ai_tools[tool.name] = tool

    def add_estimator(self, control: str, fn: Callable[..., float | None]) -> None:
        """Savings estimator for a control: fn(finding, pricing) -> USD/month or None."""
        self.estimators[control] = fn

    def add_dashboard_section(self, section_id: str, title: str, fn: Callable[..., str]) -> None:
        """Extra dashboard tab: fn(data, cfg) -> HTML (trusted plugin markup; escape any data you embed)."""
        self.dashboard_sections.append((section_id, title, fn))

    def add_mod(self, path: Path) -> None:
        self.mod_paths.append(Path(path))

    def apply(self, app: typer.Typer) -> None:
        for fn, name in self.commands:
            app.command(name)(fn)
        for sub, name in self.typers:
            app.add_typer(sub, name=name)


def _register(obj: Any, reg: Registry) -> None:
    fn = getattr(obj, "register", None)
    if not callable(fn):
        raise TypeError("plugin has no register(registry) function")
    fn(reg)


def load_plugins(reg: Registry, *, builtins: tuple[str, ...] = BUILTIN_FEATURES) -> Registry:
    """Load built-in features, then external entry-point plugins. A broken plugin never breaks the CLI."""
    disabled = {s.strip() for s in os.environ.get("SPENDLENS_DISABLE_PLUGINS", "").split(",") if s.strip()}

    for mod_name in builtins:
        short = mod_name.rsplit(".", 1)[-1]
        try:
            _register(importlib.import_module(mod_name), reg)
            reg.plugins.append(PluginInfo(short, "builtin"))
        except Exception as e:  # noqa: BLE001 - surface any plugin failure, keep CLI usable
            reg.plugins.append(PluginInfo(short, "builtin", f"{type(e).__name__}: {e}"))

    if "*" in disabled:
        return reg
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        if ep.name in disabled:
            continue
        try:
            _register(ep.load(), reg)
            reg.plugins.append(PluginInfo(ep.name, ep.value))
        except Exception as e:  # noqa: BLE001
            reg.plugins.append(PluginInfo(ep.name, ep.value, f"{type(e).__name__}: {e}"))
    return reg


registry = Registry()
