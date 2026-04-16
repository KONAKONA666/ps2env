from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import PS2EnvConfig
from .env_types import EnvContext
from .module_loading import load_module_from_path
from .policy_runtime import Policy


ActionFunction = Callable[..., Any]


@dataclass(frozen=True)
class LoadedAction:
    index: int
    name: str
    module_path: Path
    function: ActionFunction


def actions_dir(config: PS2EnvConfig) -> Path:
    return (Path(config.config_dir) / "actions").resolve()


def load_action_registry(config: PS2EnvConfig) -> tuple[LoadedAction, ...]:
    if not config.game.actions:
        return ()

    base_dir = actions_dir(config)
    loaded: list[LoadedAction] = []
    for index, name in enumerate(config.game.actions):
        module_path = (base_dir / f"{name}.py").resolve()
        if not module_path.is_file():
            raise FileNotFoundError(f"Configured action module does not exist: {module_path}")
        module = load_module_from_path(module_path, namespace="ps2env_actions")
        action_fn = getattr(module, "action", None)
        if not callable(action_fn):
            raise AttributeError(f"Action module '{module_path.name}' has no action(ctx, *args) function")
        loaded.append(
            LoadedAction(
                index=index,
                name=name,
                module_path=module_path,
                function=action_fn,
            )
        )
    return tuple(loaded)


class ConfiguredActionPolicy(Policy):
    def __init__(self, config: PS2EnvConfig) -> None:
        self._actions = load_action_registry(config)
        if not self._actions:
            raise ValueError("ConfiguredActionPolicy requires at least one game.actions entry.")

    def get_action(self, ctx: EnvContext, action: Any = None) -> dict[str, Any]:
        del ctx
        if not isinstance(action, (list, tuple)):
            raise TypeError("step() action must be a list or tuple like [action_idx, *action_args].")
        if not action:
            raise ValueError("step() action payload must contain at least an action index.")

        action_index = action[0]
        if isinstance(action_index, bool) or not isinstance(action_index, int):
            raise TypeError("step() action index must be an integer.")
        if action_index < 0 or action_index >= len(self._actions):
            raise IndexError(f"step() action index {action_index} is out of range for {len(self._actions)} configured actions.")

        loaded = self._actions[action_index]
        return {
            "index": loaded.index,
            "name": loaded.name,
            "args": tuple(action[1:]),
            "module_path": str(loaded.module_path),
        }

    def take_action(self, ctx: EnvContext, action: Any) -> None:
        if not isinstance(action, dict):
            raise TypeError("Configured action metadata must be a dictionary.")
        action_index = action.get("index")
        if not isinstance(action_index, int):
            raise TypeError("Configured action metadata is missing a valid integer index.")
        loaded = self._actions[action_index]
        args = action.get("args", ())
        if not isinstance(args, tuple):
            args = tuple(args)
        loaded.function(ctx, *args)
