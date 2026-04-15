from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

import numpy as np

from .config import SmokeConfig

if TYPE_CHECKING:
    from .session import PCSX2Session


class EnvState(enum.Enum):
    SHUTDOWN = "shutdown"
    STARTUP = "startup"
    INITIALIZATION = "initialization"
    EPISODE = "episode"
    TERMINATED = "terminated"
    TRUNCATED = "truncated"


CheckFunction = Callable[["EnvContext"], tuple[bool, dict[str, Any]]]
CallbackFunction = Callable[["EnvContext"], Any]
RewardFunction = Callable[..., float]


@dataclass(slots=True)
class EnvContext:
    config: SmokeConfig
    base_actions: "BaseActions"
    checks: dict[str, CheckFunction]
    callbacks: dict[str, CallbackFunction]
    debug_artifact_recorder: Callable[[str], str | None] | None = None
    observation: np.ndarray = field(default_factory=lambda: np.empty((0, 0, 3), dtype=np.uint8))
    frame: np.ndarray = field(default_factory=lambda: np.empty((0, 0, 3), dtype=np.uint8))
    frame_count: int = 0
    step_count: int = 0
    env_state: EnvState = EnvState.SHUTDOWN
    game_pid: int | None = None
    game_alive: bool = False
    display: str = ""
    custom: dict[str, Any] = field(default_factory=dict)
    last_update_profile: dict[str, float] = field(default_factory=dict)
    episode_start_time: float = field(default_factory=time.monotonic)
    game_vars: Any | None = None

    def set_env_state(self, env_state: EnvState) -> None:
        self.env_state = env_state

    def update(
        self,
        *,
        frame: np.ndarray,
        observation: np.ndarray,
        frame_count: int,
        game_pid: int | None,
        game_alive: bool,
        display: str,
        profile: dict[str, float] | None = None,
    ) -> "EnvContext":
        self.frame = frame
        self.observation = observation
        self.frame_count = int(frame_count)
        self.game_pid = game_pid
        self.game_alive = bool(game_alive)
        self.display = display
        self.last_update_profile = {} if profile is None else dict(profile)
        return self

    def reset_episode(self) -> None:
        self.step_count = 0
        self.custom = {}
        self.episode_start_time = time.monotonic()

    def save_debug_artifact(self, tag: str) -> str | None:
        if self.debug_artifact_recorder is None:
            return None
        return self.debug_artifact_recorder(tag)


@dataclass
class BaseActions:
    session: "PCSX2Session"
    game_fps: float

    def press_key(self, key: str) -> None:
        self.session.input.press_key(key)

    def release_key(self, key: str) -> None:
        self.session.input.release_key(key)

    def tap_key(self, key: str) -> None:
        self.session.input.tap_key(key)

    def release_all(self) -> None:
        self.session.input.release_all()

    def ensure_paused(self) -> None:
        self.session.ensure_paused()

    def wait_num_frames(self, frame_count: int) -> dict[str, Any]:
        return self.session.advance_frames(frame_count)

    def frames_from_seconds(self, seconds: float) -> int:
        return max(1, round(float(seconds) * max(self.game_fps, 1.0)))

    def wait_seconds(self, seconds: float) -> dict[str, Any]:
        return self.wait_num_frames(self.frames_from_seconds(seconds))
