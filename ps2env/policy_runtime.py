from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .env_types import EnvContext
from .module_loading import load_module_from_path, resolve_python_path


class Policy(ABC):
    @abstractmethod
    def get_action(self, ctx: EnvContext, action: Any = None) -> Any:
        raise NotImplementedError

    @abstractmethod
    def take_action(self, ctx: EnvContext, action: Any) -> None:
        raise NotImplementedError


def _discover_policy_class(module: Any, *, module_path: Path) -> type[Policy]:
    subclasses: list[type[Policy]] = []
    for _name, value in inspect.getmembers(module, inspect.isclass):
        if value is Policy:
            continue
        if issubclass(value, Policy) and value.__module__ == module.__name__:
            subclasses.append(value)
    if len(subclasses) != 1:
        raise RuntimeError(
            f"Policy module {module_path} must define exactly one concrete Policy subclass, "
            f"found {len(subclasses)}"
        )
    return subclasses[0]


def load_policy(policy_dir: str | Path, reference: str) -> Policy:
    module_path = resolve_python_path(policy_dir, reference)
    module = load_module_from_path(module_path, namespace="ps2env_policy")
    policy_cls = _discover_policy_class(module, module_path=module_path)
    try:
        return policy_cls()
    except TypeError as exc:
        raise RuntimeError(f"Policy class {policy_cls.__name__} must be instantiable without arguments") from exc
