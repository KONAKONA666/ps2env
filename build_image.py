#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


LOGGER = logging.getLogger("ps2env.build_image")
IGNORE_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
}
VALIDATION_SPECS = (
    ("game", "iso_path", "file", True),
    ("game", "bios_dir", "dir", True),
    ("game", "checks_dir", "dir", True),
    ("game", "callbacks_dir", "dir", True),
    ("game", "policy_dir", "dir", True),
    ("game", "env_utils", "file", False),
    ("savestates", "episode_start_file", "file", False),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a PS2Env per-game Docker image from an env-root config.")
    parser.add_argument(
        "--config",
        required=True,
        help="Repo-relative or absolute path to the env-root config.toml.",
    )
    parser.add_argument(
        "--base-image-tag",
        required=True,
        help="Existing base image tag to build from.",
    )
    parser.add_argument(
        "--game-image-tag",
        required=True,
        help="Final per-game image tag to build.",
    )
    parser.add_argument(
        "--build-base",
        action="store_true",
        help="Rebuild the base image before building the game image.",
    )
    parser.add_argument(
        "--build-pcsx2",
        action="store_true",
        help="Force a vendored PCSX2 AppImage rebuild before rebuilding the base image. Implies --build-base.",
    )
    return parser.parse_args()


def _find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise FileNotFoundError(f"Could not find repo root containing pyproject.toml above {start}")


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Config {path} did not parse to a TOML table.")
    return data


def _get_table(data: dict[str, Any], name: str, *, required: bool) -> dict[str, Any]:
    table = data.get(name)
    if table is None and not required:
        return {}
    if not isinstance(table, dict):
        raise ValueError(f"Missing or invalid [{name}] section in config.")
    return table


def _normalize_env_relative_path(env_root: Path, raw_value: str, *, field_name: str) -> Path:
    path = Path(raw_value)
    if path.is_absolute():
        raise ValueError(f"{field_name} must be a relative path under the env root, got absolute path: {raw_value}")
    normalized = Path(os.path.normpath(str(env_root / path)))
    try:
        normalized.relative_to(env_root)
    except ValueError as exc:
        raise ValueError(f"{field_name} must stay under the env root, got path: {raw_value}") from exc
    return normalized


def _validate_relative_target(
    env_root: Path,
    section_name: str,
    key: str,
    expected_kind: str,
    raw_value: Any,
    *,
    required: bool,
) -> None:
    field_name = f"{section_name}.{key}"
    if raw_value is None:
        if required:
            raise ValueError(f"Missing required config key: {field_name}")
        return
    if not isinstance(raw_value, str) or not raw_value.strip():
        if required:
            raise ValueError(f"{field_name} must be a non-empty relative path string.")
        return

    normalized = _normalize_env_relative_path(env_root, raw_value.strip(), field_name=field_name)
    if not normalized.exists():
        raise FileNotFoundError(f"{field_name} target does not exist: {normalized}")
    if expected_kind == "file" and not normalized.is_file():
        raise FileNotFoundError(f"{field_name} must resolve to a file: {normalized}")
    if expected_kind == "dir" and not normalized.is_dir():
        raise FileNotFoundError(f"{field_name} must resolve to a directory: {normalized}")


def _validate_actions(env_root: Path, game_table: dict[str, Any]) -> None:
    raw_actions = game_table.get("actions", [])
    if raw_actions in (None, []):
        return
    if not isinstance(raw_actions, list):
        raise ValueError("game.actions must be a list of action module names.")

    actions_dir = env_root / "actions"
    if not actions_dir.is_dir():
        raise FileNotFoundError(f"game.actions requires an actions directory at: {actions_dir}")

    seen: set[str] = set()
    for raw_action in raw_actions:
        if not isinstance(raw_action, str) or not raw_action.strip():
            raise ValueError("game.actions entries must be non-empty strings.")
        action = raw_action.strip()
        candidate = Path(action)
        if candidate.suffix or candidate.name != action or candidate.parent != Path("."):
            raise ValueError("game.actions entries must be simple module names without directories or extensions.")
        if action in seen:
            raise ValueError(f"game.actions contains a duplicate action name: {action}")
        seen.add(action)

        module_path = actions_dir / f"{action}.py"
        if not module_path.is_file():
            raise FileNotFoundError(f"Configured action module does not exist: {module_path}")


def _copy_ignore(_: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORE_NAMES}


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, symlinks=False, ignore=_copy_ignore, dirs_exist_ok=True)


def _stage_build_context(repo_root: Path, env_root: Path, env_rel: Path) -> Path:
    context_root = Path(tempfile.mkdtemp(prefix="ps2env-build-image-"))
    payload_root = context_root / "payload" / "opt" / "ps2env"

    shutil.copy2(repo_root / "Dockerfile.game", context_root / "Dockerfile.game")
    shutil.copy2(repo_root / "pyproject.toml", context_root / "pyproject.toml")
    _copy_tree(repo_root / "ps2env", context_root / "ps2env")
    _copy_tree(repo_root / "docker", context_root / "docker")
    _copy_tree(env_root, payload_root / env_rel)

    return context_root


def _validate_config(repo_root: Path, config_path: Path) -> tuple[Path, Path]:
    config_path = config_path.expanduser().resolve()
    if config_path.name != "config.toml":
        raise ValueError(f"Config path must point to config.toml, got: {config_path}")
    try:
        config_rel = config_path.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"Config path must live under repo root {repo_root}: {config_path}") from exc

    env_root = config_path.parent
    data = _load_toml(config_path)

    for section_name, key, expected_kind, required in VALIDATION_SPECS:
        table = _get_table(data, section_name, required=section_name == "game")
        _validate_relative_target(
            env_root,
            section_name,
            key,
            expected_kind,
            table.get(key),
            required=required,
        )

    _validate_actions(env_root, _get_table(data, "game", required=True))

    return config_path, config_rel


def _ensure_docker_prereqs(base_image_tag: str) -> None:
    if shutil.which("docker") is None:
        raise RuntimeError("docker is required to build the game image.")
    subprocess.run(
        ["docker", "image", "inspect", base_image_tag],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _build_base_image(repo_root: Path, *, base_image_tag: str, force_appimage_build: bool) -> None:
    script_path = repo_root / "scripts" / "build-base-image.sh"
    command = [str(script_path), "--tag", base_image_tag]
    if force_appimage_build:
        command.append("--force-appimage-build")
    subprocess.run(command, check=True)


def _build_image(context_root: Path, *, base_image_tag: str, game_image_tag: str) -> None:
    subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(context_root / "Dockerfile.game"),
            "--build-arg",
            f"BASE_IMAGE={base_image_tag}",
            "-t",
            game_image_tag,
            str(context_root),
        ],
        check=True,
    )


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    build_base = bool(args.build_base or args.build_pcsx2)

    repo_root = _find_repo_root(Path(__file__))
    config_path, config_rel = _validate_config(repo_root, Path(args.config))
    env_root = config_path.parent

    if shutil.which("docker") is None:
        raise RuntimeError("docker is required to build the game image.")
    LOGGER.info("Using repo root: %s", repo_root)
    LOGGER.info("Using env config: %s", config_path)
    LOGGER.info("Container config path: /opt/ps2env/%s", config_rel.as_posix())
    LOGGER.info("Base image: %s", args.base_image_tag)
    LOGGER.info("Final image: %s", args.game_image_tag)
    if build_base:
        if args.build_pcsx2:
            LOGGER.info("Rebuilding vendored PCSX2 AppImage and base image")
        else:
            LOGGER.info("Rebuilding base image")
        _build_base_image(
            repo_root,
            base_image_tag=args.base_image_tag,
            force_appimage_build=bool(args.build_pcsx2),
        )
    _ensure_docker_prereqs(args.base_image_tag)

    context_root = _stage_build_context(repo_root, env_root, config_rel.parent)
    try:
        LOGGER.info("Staged build context at %s", context_root)
        _build_image(
            context_root,
            base_image_tag=args.base_image_tag,
            game_image_tag=args.game_image_tag,
        )
    finally:
        shutil.rmtree(context_root, ignore_errors=True)

    LOGGER.info("Built image %s", args.game_image_tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
