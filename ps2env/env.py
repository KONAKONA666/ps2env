from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

from .capture import capture_single_frame
from .config import SmokeConfig, load_config
from .env_types import BaseActions, CallbackFunction, CheckFunction, EnvContext, EnvState
from .gpu import discover_discrete_nvidia_adapters
from .hooks import invoke_reward, load_callback_registry, load_check_registry, load_reward_function, load_step_checks, resolve_check
from .policy_runtime import Policy, load_policy
from .session import PCSX2Session


class PS2Env:
    def __init__(
        self,
        config: SmokeConfig | str | Path,
        *,
        worker_id: int = 0,
        output_root: str | Path = "/workspace/output",
        run_id: str = "env-run",
    ) -> None:
        self.config = config if isinstance(config, SmokeConfig) else load_config(config)
        self.worker_id = worker_id
        self.output_root = Path(output_root)
        self.run_id = run_id
        self._state = EnvState.SHUTDOWN

        self._checks = load_check_registry(self.config)
        self._callbacks = load_callback_registry(self.config)
        self._startup_check = resolve_check(self.config, self.config.game.startup_check)
        self._episode_check = resolve_check(self.config, self.config.game.episode_check)
        self._step_checks = load_step_checks(self.config)
        self._reward_fn = load_reward_function(self.config)
        self._init_policy = load_policy(self.config.game.policy_dir, "init_policy")
        self._reset_policy = load_policy(self.config.game.policy_dir, "reset_policy")
        self._step_policy = load_policy(self.config.game.policy_dir, "step_policy")

        self.session: PCSX2Session | None = None
        self.base_actions: BaseActions | None = None
        self.ctx: EnvContext | None = None

    @property
    def state(self) -> EnvState:
        return self._state

    def _set_state(self, state: EnvState) -> None:
        self._state = state
        if self.ctx is not None:
            self.ctx.set_env_state(state)

    def _require_state(self, method: str, allowed: tuple[EnvState, ...]) -> None:
        if self._state not in allowed:
            names = ", ".join(item.value for item in allowed)
            raise RuntimeError(f"{method}() is only valid from: {names}. Current state: {self._state.value}")

    def _build_info(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        info: dict[str, Any] = {
            "env_state": self._state.value,
        }
        if self.ctx is not None:
            info.update(
                {
                    "frame_count": self.ctx.frame_count,
                    "step_count": self.ctx.step_count,
                    "game_pid": self.ctx.game_pid,
                    "game_alive": self.ctx.game_alive,
                    "display": self.ctx.display,
                }
            )
            if self.ctx.last_update_profile:
                info["ctx_update_profile"] = dict(self.ctx.last_update_profile)
        if extra:
            info.update(extra)
        return info

    def _initialize_runtime(self) -> None:
        adapters = discover_discrete_nvidia_adapters()
        adapter = adapters[0]
        self.session = PCSX2Session(
            config=self.config,
            worker_id=self.worker_id,
            run_id=self.run_id,
            output_root=self.output_root,
            adapter=adapter,
        )
        self.session.start()
        self.base_actions = BaseActions(self.session, game_fps=float(self.config.capture.game_fps))
        self.ctx = EnvContext(
            config=self.config,
            base_actions=self.base_actions,
            checks=self._checks,
            callbacks=self._callbacks,
            debug_artifact_recorder=self._save_debug_artifact,
        )
        self._capture_and_update()

    def _runtime_ready(self) -> None:
        if self.session is None or self.base_actions is None or self.ctx is None:
            raise RuntimeError("Runtime components are not initialized.")

    def _capture_and_update(self) -> tuple[Any, Any]:
        self._runtime_ready()
        assert self.session is not None and self.ctx is not None
        total_start = time.monotonic()
        frame, observation = self.session.capture_current_frame()
        profile = {"capture_ms": (time.monotonic() - total_start) * 1000.0}
        self.ctx.update(
            frame=frame,
            observation=observation,
            frame_count=self.session.frame_count,
            game_pid=self.session.get_pid(),
            game_alive=self.session.is_game_alive(),
            display=self.session.display,
            profile=profile,
        )
        return frame, observation

    def _save_debug_artifact(self, tag: str) -> str | None:
        if self.session is None:
            return None
        return self.session.save_debug_artifact(tag)

    def _apply_after_action(self, *, before_wait: bool) -> None:
        if self.base_actions is None:
            return
        if self.config.stepping.after_action == "press" and before_wait:
            self.base_actions.release_all()
        if self.config.stepping.after_action == "hold" and not before_wait:
            self.base_actions.release_all()

    def _advance_with_policy(self, policy: Policy, *, action_input: Any, n_frames: int) -> tuple[Any, dict[str, Any]]:
        self._runtime_ready()
        assert self.ctx is not None and self.base_actions is not None
        resolved_action = policy.get_action(self.ctx, action_input)
        policy.take_action(self.ctx, resolved_action)
        self._apply_after_action(before_wait=True)
        frame_profile = self.base_actions.wait_num_frames(n_frames)
        self._apply_after_action(before_wait=False)
        return resolved_action, frame_profile

    def _wait_for_check(
        self,
        check_fn: CheckFunction | None,
        *,
        policy: Policy | None,
        stage_name: str,
    ) -> tuple[bool, dict[str, Any]]:
        self._runtime_ready()
        assert self.ctx is not None
        start_frame = self.ctx.frame_count
        max_frames = self.config.lifecycle.timeout_frames
        while True:
            self._capture_and_update()
            if check_fn is None:
                return True, {}
            triggered, check_info = check_fn(self.ctx)
            if triggered:
                return triggered, check_info
            waited = self.ctx.frame_count - start_frame
            if waited >= max_frames:
                raise TimeoutError(f"{stage_name} exceeded lifecycle.timeout_frames={max_frames}")
            if policy is None:
                _, _ = self._advance_with_policy(NoOpPolicy(), action_input=None, n_frames=self.config.lifecycle.frames_per_loop)
            else:
                _, _ = self._advance_with_policy(policy, action_input=None, n_frames=self.config.lifecycle.frames_per_loop)

    def start(self) -> dict[str, Any]:
        self._require_state("start", (EnvState.SHUTDOWN,))
        try:
            self._initialize_runtime()
            self._set_state(EnvState.STARTUP)
            triggered, check_info = self._wait_for_check(self._startup_check, policy=None, stage_name="startup_check")
            return self._build_info({"startup_check_triggered": triggered, "startup_check_info": check_info})
        except Exception:
            self._save_debug_artifact("start_error")
            self.kill()
            raise

    def init(self) -> tuple[Any, dict[str, Any]]:
        self._require_state("init", (EnvState.STARTUP,))
        try:
            self._runtime_ready()
            assert self.ctx is not None
            self.ctx.reset_episode()
            self._set_state(EnvState.INITIALIZATION)
            triggered, check_info = self._wait_for_check(self._episode_check, policy=self._init_policy, stage_name="init_check")
            self._capture_and_update()
            self._set_state(EnvState.EPISODE)
            return self.ctx.observation, self._build_info({"episode_check_triggered": triggered, "episode_check_info": check_info})
        except Exception:
            self._save_debug_artifact("init_error")
            raise

    def reset(self) -> tuple[Any, dict[str, Any]]:
        self._require_state("reset", (EnvState.EPISODE, EnvState.TERMINATED, EnvState.TRUNCATED))
        try:
            self._runtime_ready()
            assert self.ctx is not None
            self.ctx.reset_episode()
            self._set_state(EnvState.INITIALIZATION)
            triggered, check_info = self._wait_for_check(self._episode_check, policy=self._reset_policy, stage_name="reset_check")
            self._capture_and_update()
            self._set_state(EnvState.EPISODE)
            return self.ctx.observation, self._build_info({"episode_check_triggered": triggered, "episode_check_info": check_info})
        except Exception:
            self._save_debug_artifact("reset_error")
            raise

    def step(self, action: Any) -> tuple[Any, float, bool, bool, dict[str, Any]]:
        self._require_state("step", (EnvState.EPISODE,))
        try:
            self._runtime_ready()
            assert self.ctx is not None
            step_start = time.monotonic()
            resolved_action, frame_profile = self._advance_with_policy(
                self._step_policy,
                action_input=action,
                n_frames=self.config.stepping.n_frames_per_step,
            )
            self._capture_and_update()
            self.ctx.step_count += 1

            terminated = False
            truncated = False
            info: dict[str, Any] = {
                "action": resolved_action,
                "frame_profile": frame_profile,
                "requested_frames": self.config.stepping.n_frames_per_step,
                "advanced_frames": frame_profile["advanced_frames"],
            }

            checks_start = time.monotonic()
            for name, check_fn in self._step_checks:
                triggered, check_info = check_fn(self.ctx)
                if not triggered:
                    continue
                info.setdefault("checks", []).append(name)
                info.setdefault("check_info", {})[name] = dict(check_info)
                info.update(check_info)
                if check_info.get("terminated"):
                    terminated = True
                    break
                if check_info.get("truncated"):
                    truncated = True
                    break
            checks_ms = (time.monotonic() - checks_start) * 1000.0

            reward_start = time.monotonic()
            reward = invoke_reward(self._reward_fn, self.ctx, info)
            reward_ms = (time.monotonic() - reward_start) * 1000.0

            if terminated:
                self._set_state(EnvState.TERMINATED)
            elif truncated:
                self._set_state(EnvState.TRUNCATED)
            else:
                self._set_state(EnvState.EPISODE)

            total_ms = (time.monotonic() - step_start) * 1000.0
            info["profile"] = {
                "total_ms": total_ms,
                "checks_ms": checks_ms,
                "reward_ms": reward_ms,
                "ctx_update_profile": dict(self.ctx.last_update_profile),
                "frame_count": self.ctx.frame_count,
                "step_count": self.ctx.step_count,
            }
            return self.ctx.observation, reward, terminated, truncated, self._build_info(info)
        except Exception:
            self._save_debug_artifact("step_error")
            raise

    def kill(self) -> None:
        if self.base_actions is not None:
            try:
                self.base_actions.release_all()
            except Exception:
                pass
        if self.session is not None:
            self.session.stop()
        self.session = None
        self.base_actions = None
        self.ctx = None
        self._set_state(EnvState.SHUTDOWN)


class NoOpPolicy(Policy):
    def get_action(self, ctx: EnvContext, action: Any = None) -> Any:
        return None

    def take_action(self, ctx: EnvContext, action: Any) -> None:
        return None
