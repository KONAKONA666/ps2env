from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from .config import PS2EnvConfig
from .env_types import CallbackFunction, CheckFunction, RewardFunction
from .module_loading import load_module_from_path, resolve_python_path


def _iter_python_modules(directory: str | Path) -> list[Path]:
    base = Path(directory)
    if not base.exists():
        return []
    return sorted(path for path in base.glob("*.py") if path.name != "__init__.py")


def load_check_registry(config: PS2EnvConfig) -> dict[str, CheckFunction]:
    checks: dict[str, CheckFunction] = {}
    for module_path in _iter_python_modules(config.game.checks_dir):
        module = load_module_from_path(module_path, namespace="ps2env_checks")
        check_fn = getattr(module, "check", None)
        if not callable(check_fn):
            raise AttributeError(f"Check module '{module_path.name}' has no check(ctx) function")
        checks[module_path.stem] = check_fn
    return checks


def load_callback_registry(config: PS2EnvConfig) -> dict[str, CallbackFunction]:
    callbacks: dict[str, CallbackFunction] = {}
    for module_path in _iter_python_modules(config.game.callbacks_dir):
        module = load_module_from_path(module_path, namespace="ps2env_callbacks")
        for attr_name in ("callback", "step", module_path.stem):
            value = getattr(module, attr_name, None)
            if callable(value):
                callbacks[module_path.stem] = value
                break
        else:
            raise AttributeError(
                f"Callback module '{module_path.name}' has no callback(ctx), step(ctx), "
                f"or {module_path.stem}(ctx) function"
            )
    return callbacks


def resolve_check(config: PS2EnvConfig, reference: str | None) -> CheckFunction | None:
    if not reference:
        return None
    module_path = resolve_python_path(config.game.checks_dir, reference)
    module = load_module_from_path(module_path, namespace="ps2env_resolved_checks")
    check_fn = getattr(module, "check", None)
    if not callable(check_fn):
        raise AttributeError(f"Check module '{reference}' has no check(ctx) function")
    return check_fn


def load_step_checks(config: PS2EnvConfig) -> list[tuple[str, CheckFunction]]:
    loaded: list[tuple[str, CheckFunction]] = []
    for reference in config.game.step_checks:
        check_fn = resolve_check(config, reference)
        if check_fn is not None:
            loaded.append((reference, check_fn))
    return loaded


def load_reward_function(config: PS2EnvConfig) -> RewardFunction:
    if not config.game.env_utils:
        return lambda ctx, info=None: 0.0
    module = load_module_from_path(config.game.env_utils, namespace="ps2env_env_utils")
    reward_fn = getattr(module, "compute_reward", None) or getattr(module, "compute", None)
    if not callable(reward_fn):
        raise AttributeError(f"Env utils module '{config.game.env_utils}' has no compute_reward(ctx, info) or compute(ctx, info)")
    return reward_fn


def invoke_reward(reward_fn: RewardFunction, ctx: Any, info: dict[str, Any]) -> float:
    parameter_count = len(inspect.signature(reward_fn).parameters)
    if parameter_count >= 2:
        return float(reward_fn(ctx, info))
    return float(reward_fn(ctx))
