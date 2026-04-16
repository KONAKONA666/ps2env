from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


@dataclass(frozen=True)
class GameConfig:
    iso_path: str
    bios_dir: str
    bios_file: str | None
    fastboot: bool
    startup_check: str | None
    episode_check: str | None
    step_checks: tuple[str, ...]
    checks_dir: str
    callbacks_dir: str
    policy_dir: str
    env_utils: str | None


@dataclass(frozen=True)
class WorkersConfig:
    count: int
    display_base: int
    pine_slot_base: int


@dataclass(frozen=True)
class GPUConfig:
    renderer: str
    vendor: str


@dataclass(frozen=True)
class InputConfig:
    pause_hotkey: str | None
    frame_advance_hotkey: str | None


@dataclass(frozen=True)
class SavestatesConfig:
    episode_start_file: str | None
    episode_start_slot: int


@dataclass(frozen=True)
class CaptureConfig:
    width: int
    height: int
    framerate: int
    game_fps: int
    observation_shape: tuple[int, int]


@dataclass(frozen=True)
class SteppingConfig:
    n_frames_per_step: int
    after_action: str
    capture_action: bool


@dataclass(frozen=True)
class LifecycleConfig:
    frames_per_loop: int
    timeout_frames: int


@dataclass(frozen=True)
class LoggingConfig:
    level: str


@dataclass(frozen=True)
class PS2EnvConfig:
    game: GameConfig
    workers: WorkersConfig
    gpu: GPUConfig
    input: InputConfig
    savestates: SavestatesConfig
    capture: CaptureConfig
    stepping: SteppingConfig
    lifecycle: LifecycleConfig
    logging: LoggingConfig
    config_path: str
    config_dir: str


def _require_section(data: dict[str, Any], name: str) -> dict[str, Any]:
    section = data.get(name)
    if not isinstance(section, dict):
        raise ValueError(f"Missing or invalid [{name}] section in config.")
    return section


def _optional_section(data: dict[str, Any], name: str) -> dict[str, Any]:
    section = data.get(name, {})
    if not isinstance(section, dict):
        raise ValueError(f"Invalid [{name}] section in config.")
    return section


def _get_str(section: dict[str, Any], key: str, *, default: str | None = None) -> str:
    value = section.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Expected non-empty string for '{key}'.")
    return value


def _get_optional_str(section: dict[str, Any], key: str, *, default: str | None = None) -> str | None:
    value = section.get(key, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected string for '{key}'.")
    stripped = value.strip()
    return stripped or None


def _get_int(section: dict[str, Any], key: str, *, default: int | None = None) -> int:
    value = section.get(key, default)
    if not isinstance(value, int):
        raise ValueError(f"Expected integer for '{key}'.")
    return value


def _get_bool(section: dict[str, Any], key: str, *, default: bool | None = None) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Expected boolean for '{key}'.")
    return value


def _get_str_tuple(section: dict[str, Any], key: str, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = section.get(key, list(default))
    if not isinstance(value, list):
        raise ValueError(f"Expected list for '{key}'.")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"Expected non-empty string items for '{key}'.")
        items.append(item.strip())
    return tuple(items)


def _get_pair(section: dict[str, Any], key: str, *, default: tuple[int, int]) -> tuple[int, int]:
    value = section.get(key, list(default))
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(item, int) and item > 0 for item in value)
    ):
        raise ValueError(f"Expected two positive integers for '{key}'.")
    return int(value[0]), int(value[1])


def _resolve_config_path(base_dir: Path, value: str | None) -> str | None:
    if not value:
        return value
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((base_dir / path).resolve())


def _reject_removed_keys(section_name: str, section: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key in section:
            raise ValueError(f"Config key '{section_name}.{key}' was removed and is no longer supported.")


def load_config(path: str | Path) -> PS2EnvConfig:
    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    base_dir = config_path.parent

    game = _require_section(data, "game")
    workers = _require_section(data, "workers")
    gpu = _require_section(data, "gpu")
    input_section = _optional_section(data, "input")
    savestates = _optional_section(data, "savestates")
    capture = _require_section(data, "capture")
    stepping = _optional_section(data, "stepping")
    lifecycle = _optional_section(data, "lifecycle")
    logging = _require_section(data, "logging")

    _reject_removed_keys("workers", workers, ("duration_seconds",))
    _reject_removed_keys("input", input_section, ("action_interval_ms", "press_duration_ms", "action_labels"))

    n_frames_per_step = stepping.get("n_frames_per_step")
    if n_frames_per_step is None:
        n_frames_per_step = stepping.get("n_frames_per_action", 4)
    if not isinstance(n_frames_per_step, int) or n_frames_per_step < 1:
        raise ValueError("stepping.n_frames_per_step must be an integer >= 1.")

    episode_start_file = _resolve_config_path(base_dir, _get_optional_str(savestates, "episode_start_file"))
    episode_start_slot = _get_int(savestates, "episode_start_slot", default=1)
    if episode_start_slot < 0:
        raise ValueError("savestates.episode_start_slot must be >= 0.")

    return PS2EnvConfig(
        game=GameConfig(
            iso_path=_resolve_config_path(base_dir, _get_str(game, "iso_path")) or "",
            bios_dir=_resolve_config_path(base_dir, _get_str(game, "bios_dir")) or "",
            bios_file=_get_optional_str(game, "bios_file"),
            fastboot=_get_bool(game, "fastboot"),
            startup_check=_get_optional_str(game, "startup_check"),
            episode_check=_get_optional_str(game, "episode_check"),
            step_checks=_get_str_tuple(game, "step_checks"),
            checks_dir=_resolve_config_path(base_dir, _get_str(game, "checks_dir", default="checks")) or "",
            callbacks_dir=_resolve_config_path(base_dir, _get_str(game, "callbacks_dir", default="callbacks")) or "",
            policy_dir=_resolve_config_path(base_dir, _get_str(game, "policy_dir", default="policy")) or "",
            env_utils=_resolve_config_path(base_dir, _get_optional_str(game, "env_utils")),
        ),
        workers=WorkersConfig(
            count=_get_int(workers, "count"),
            display_base=_get_int(workers, "display_base"),
            pine_slot_base=_get_int(workers, "pine_slot_base"),
        ),
        gpu=GPUConfig(
            renderer=_get_str(gpu, "renderer"),
            vendor=_get_str(gpu, "vendor"),
        ),
        input=InputConfig(
            pause_hotkey=_get_optional_str(input_section, "pause_hotkey"),
            frame_advance_hotkey=_get_optional_str(input_section, "frame_advance_hotkey"),
        ),
        savestates=SavestatesConfig(
            episode_start_file=episode_start_file,
            episode_start_slot=int(episode_start_slot),
        ),
        capture=CaptureConfig(
            width=_get_int(capture, "width"),
            height=_get_int(capture, "height"),
            framerate=_get_int(capture, "framerate"),
            game_fps=_get_int(capture, "game_fps", default=_get_int(capture, "framerate")),
            observation_shape=_get_pair(capture, "observation_shape", default=(_get_int(capture, "height"), _get_int(capture, "width"))),
        ),
        stepping=SteppingConfig(
            n_frames_per_step=int(n_frames_per_step),
            after_action=_get_str(stepping, "after_action", default="hold"),
            capture_action=_get_bool(stepping, "capture_action", default=False),
        ),
        lifecycle=LifecycleConfig(
            frames_per_loop=_get_int(lifecycle, "frames_per_loop", default=4),
            timeout_frames=_get_int(lifecycle, "timeout_frames", default=1440),
        ),
        logging=LoggingConfig(
            level=_get_str(logging, "level"),
        ),
        config_path=str(config_path),
        config_dir=str(base_dir),
    )


def apply_runtime_overrides(
    config: PS2EnvConfig,
    *,
    workers: int | None = None,
    game_path: str | None = None,
    bios_dir: str | None = None,
) -> PS2EnvConfig:
    updated = config
    if workers is not None:
        updated = replace(updated, workers=replace(updated.workers, count=workers))
    if game_path is not None or bios_dir is not None:
        updated = replace(
            updated,
            game=replace(
                updated.game,
                iso_path=game_path or updated.game.iso_path,
                bios_dir=bios_dir or updated.game.bios_dir,
            ),
        )
    return updated
