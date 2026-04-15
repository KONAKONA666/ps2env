from __future__ import annotations

import random
import subprocess
import time
from dataclasses import dataclass, field


def _display_env(display: str) -> dict[str, str]:
    return {"DISPLAY": display}


def _best_effort_activate(display: str, window_id: int) -> None:
    try:
        subprocess.run(
            ["xdotool", "windowactivate", "--sync", str(window_id)],
            check=True,
            capture_output=True,
            text=True,
            env=_display_env(display),
        )
    except subprocess.CalledProcessError:
        pass


SMOKE_ACTION_KEYS: dict[str, str | None] = {
    "noop": None,
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "cross": "z",
    "circle": "x",
    "square": "c",
    "triangle": "s",
    "start": "Return",
}


def random_smoke_action(rng: random.Random) -> str:
    return rng.choice(tuple(SMOKE_ACTION_KEYS))


@dataclass
class X11InputController:
    display: str
    window_id: int
    held_keys: set[str] = field(default_factory=set)

    def activate_window(self) -> None:
        _best_effort_activate(self.display, self.window_id)

    def press_key(self, key: str) -> None:
        self.activate_window()
        subprocess.run(
            ["xdotool", "keydown", "--window", str(self.window_id), key],
            check=True,
            capture_output=True,
            text=True,
            env=_display_env(self.display),
        )
        self.held_keys.add(key)

    def release_key(self, key: str) -> None:
        if key not in self.held_keys:
            return
        self.activate_window()
        subprocess.run(
            ["xdotool", "keyup", "--window", str(self.window_id), key],
            check=True,
            capture_output=True,
            text=True,
            env=_display_env(self.display),
        )
        self.held_keys.discard(key)

    def tap_key(self, key: str) -> None:
        self.activate_window()
        subprocess.run(
            ["xdotool", "key", "--window", str(self.window_id), key],
            check=True,
            capture_output=True,
            text=True,
            env=_display_env(self.display),
        )

    def release_all(self) -> None:
        for key in list(self.held_keys):
            self.release_key(key)


def send_key_tap(display: str, window_id: int, key: str, hold_ms: int) -> None:
    env = _display_env(display)
    _best_effort_activate(display, window_id)
    subprocess.run(
        ["xdotool", "keydown", "--window", str(window_id), key],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    time.sleep(hold_ms / 1000.0)
    subprocess.run(
        ["xdotool", "keyup", "--window", str(window_id), key],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def send_hotkey(display: str, window_id: int, key: str) -> None:
    env = _display_env(display)
    _best_effort_activate(display, window_id)
    subprocess.run(
        ["xdotool", "key", "--window", str(window_id), key],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
