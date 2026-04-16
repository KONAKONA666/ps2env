from __future__ import annotations

import ast
import json
from typing import Any


def parse_actions_literal(raw: str) -> tuple[Any, ...]:
    try:
        value = ast.literal_eval(raw)
    except (SyntaxError, ValueError) as exc:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as json_exc:
            raise ValueError(f"Failed to parse --actions literal: {exc}") from json_exc

    if not isinstance(value, (list, tuple)):
        raise ValueError("--actions must be a Python list or tuple literal.")
    if not value:
        raise ValueError("--actions must contain at least one action.")
    return tuple(value)
