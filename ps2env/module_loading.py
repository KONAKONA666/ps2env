from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path
from types import ModuleType


_MODULE_COUNTER = itertools.count()


def resolve_python_path(directory: str | Path, reference: str) -> Path:
    base = Path(directory)
    candidate = Path(reference)
    if not candidate.suffix:
        candidate = candidate.with_suffix(".py")
    if not candidate.is_absolute():
        candidate = base / candidate
    candidate = candidate.resolve()
    if not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate


def load_module_from_path(path: str | Path, *, namespace: str) -> ModuleType:
    module_path = Path(path).resolve()
    module_name = f"{namespace}_{module_path.stem}_{next(_MODULE_COUNTER)}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to load module spec from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
