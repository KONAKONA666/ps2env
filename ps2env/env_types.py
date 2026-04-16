from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

import numpy as np

from .config import PS2EnvConfig
from .controller_mapping import NAMED_CONTROL_KEYS

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
    config: PS2EnvConfig
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
    _tracked_wait_profiles: list[dict[str, Any]] | None = field(default=None, init=False, repr=False)

    def press_key(self, key: str) -> None:
        self.session.input.press_key(key)

    def release_key(self, key: str) -> None:
        self.session.input.release_key(key)

    def tap_key(self, key: str) -> None:
        self.session.input.tap_key(key)

    def release_all(self) -> None:
        self.session.input.release_all()

    def begin_wait_tracking(self) -> None:
        self._tracked_wait_profiles = []

    def finish_wait_tracking(self) -> dict[str, Any]:
        profiles = list(self._tracked_wait_profiles or [])
        self._tracked_wait_profiles = None
        return _aggregate_wait_profiles(profiles)

    def discard_wait_tracking(self) -> None:
        self._tracked_wait_profiles = None

    def ensure_paused(self) -> None:
        self.session.ensure_paused()

    def wait_num_frames(self, frame_count: int) -> dict[str, Any]:
        profile = self.session.advance_frames(frame_count)
        if self._tracked_wait_profiles is not None:
            self._tracked_wait_profiles.append(dict(profile))
        return profile

    def frames_from_seconds(self, seconds: float) -> int:
        return max(1, round(float(seconds) * max(self.game_fps, 1.0)))

    def wait_seconds(self, seconds: float) -> dict[str, Any]:
        return self.wait_num_frames(self.frames_from_seconds(seconds))

    def save_state_slot(self, slot: int) -> None:
        self.session.save_state_slot(slot)

    def load_state_slot(self, slot: int) -> None:
        self.session.load_state_slot(slot)


def _aggregate_wait_profiles(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "requested_frames": sum(int(profile.get("requested_frames", 0)) for profile in profiles),
        "advanced_frames": sum(int(profile.get("advanced_frames", 0)) for profile in profiles),
        "frame_transitions_observed": sum(int(profile.get("frame_transitions_observed", 0)) for profile in profiles),
        "status_polls": sum(int(profile.get("status_polls", 0)) for profile in profiles),
        "total_ms": sum(float(profile.get("total_ms", 0.0)) for profile in profiles),
        "wait_count": len(profiles),
        "wait_profiles": profiles,
    }


def _make_press(control_key: str) -> Callable[[BaseActions], None]:
    def _press(self: BaseActions) -> None:
        self.press_key(control_key)

    return _press


def _make_release(control_key: str) -> Callable[[BaseActions], None]:
    def _release(self: BaseActions) -> None:
        self.release_key(control_key)

    return _release


def _make_tap(control_key: str) -> Callable[[BaseActions], None]:
    def _tap(self: BaseActions) -> None:
        self.tap_key(control_key)

    return _tap


for _control_name, _control_key in NAMED_CONTROL_KEYS.items():
    setattr(BaseActions, f"press_{_control_name}", _make_press(_control_key))
    setattr(BaseActions, f"release_{_control_name}", _make_release(_control_key))
    setattr(BaseActions, f"tap_{_control_name}", _make_tap(_control_key))
