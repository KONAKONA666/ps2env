#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ps2env.config import load_config
from ps2env.test_run_common import parse_actions_literal


CONTAINER_APP_ROOT = "/opt/ps2env"
CONTAINER_OUTPUT_ROOT = "/workspace/output"
CONTAINER_CACHE_ROOT = "/workspace/cache"


def _default_cache_dir() -> Path:
    if "PS2ENV_CACHE_DIR" in os.environ:
        return Path(os.environ["PS2ENV_CACHE_DIR"]).expanduser()
    if "XDG_CACHE_HOME" in os.environ:
        return Path(os.environ["XDG_CACHE_HOME"]).expanduser() / "ps2env"
    return Path.home() / ".cache" / "ps2env"


def _default_run_id() -> str:
    return f"test-run-{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _build_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("ps2env.test_run.host")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def _find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise FileNotFoundError(f"Could not find repo root containing pyproject.toml above {start}")


def _resolve_gpu_index() -> str:
    if os.environ.get("PS2ENV_GPU_LIST"):
        values = [item.strip() for item in os.environ["PS2ENV_GPU_LIST"].split(",") if item.strip()]
        if not values:
            raise RuntimeError("PS2ENV_GPU_LIST is set but contains no GPU indices.")
        return values[0]

    if shutil.which("nvidia-smi") is None:
        raise RuntimeError("nvidia-smi is required unless PS2ENV_GPU_LIST is set.")

    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
        check=True,
        capture_output=True,
        text=True,
    )
    indices = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not indices:
        raise RuntimeError("No NVIDIA GPUs were detected by nvidia-smi.")
    return indices[0]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the threaded PS2Env Docker test runner.")
    parser.add_argument("--config", required=True, help="Path to the environment TOML config.")
    parser.add_argument("--actions", required=True, help="Python literal describing the action pool.")
    parser.add_argument("--num-steps", type=int, required=True, help="Completed step() calls per worker.")
    parser.add_argument("--reset-steps", type=int, required=True, help="Reset every N completed steps per worker. <=0 disables.")
    parser.add_argument("--restart-steps", type=int, required=True, help="Restart every N completed steps per worker. <=0 disables.")
    parser.add_argument("--n-parallel-starts", type=int, required=True, help="Maximum concurrent start() calls.")
    parser.add_argument("--num-workers", type=int, required=True, help="Number of workers to run.")
    parser.add_argument("--outdir", required=True, help="Output directory for logs and artifacts.")
    parser.add_argument("--image", default="ps2env-smoke:latest", help="Docker image tag.")
    parser.add_argument("--run-id", default=_default_run_id(), help="Internal run identifier.")
    parser.add_argument("--seed", type=int, default=7, help="Base RNG seed.")
    parser.add_argument("--cache-dir", default=str(_default_cache_dir()), help="Host cache directory for Docker mounts.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if shutil.which("docker") is None:
        raise RuntimeError("docker is required to run test_run.py.")

    outdir = Path(args.outdir).expanduser().resolve()
    cache_dir = Path(args.cache_dir).expanduser().resolve()

    shutil.rmtree(outdir, ignore_errors=True)
    outdir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    logger = _build_logger(outdir / "runner.log")
    try:
        parse_actions_literal(args.actions)
        config_path = Path(args.config).expanduser().resolve()
        _ = load_config(config_path)
        repo_root = _find_repo_root(config_path)
        try:
            config_rel = config_path.relative_to(repo_root)
        except ValueError as exc:
            raise ValueError(f"Config path {config_path} must live under repo root {repo_root}") from exc
        gpu_index = _resolve_gpu_index()
    except Exception as exc:
        logger.error("%s", exc)
        return 1

    container_log_path = outdir / "container-env.log"
    container_config = f"{CONTAINER_APP_ROOT}/{config_rel.as_posix()}"
    container_name = re.sub(r"[^a-zA-Z0-9_.-]", "-", f"ps2env-test-{args.run_id}")

    logger.info("Running Docker test runner")
    logger.info("  Image:      %s", args.image)
    logger.info("  Config:     %s", config_path)
    logger.info("  Output dir: %s", outdir)
    logger.info("  Cache dir:  %s", cache_dir)
    logger.info("  GPU:        %s", gpu_index)
    logger.info("  Run id:     %s", args.run_id)

    runtime_cmd = (
        "/opt/ps2env/install-nvidia-display-driver.sh && "
        "python3 -m ps2env.test_run_runtime "
        f"--config {shlex.quote(container_config)} "
        f"--actions {shlex.quote(args.actions)} "
        f"--num-steps {shlex.quote(str(args.num_steps))} "
        f"--reset-steps {shlex.quote(str(args.reset_steps))} "
        f"--restart-steps {shlex.quote(str(args.restart_steps))} "
        f"--n-parallel-starts {shlex.quote(str(args.n_parallel_starts))} "
        f"--num-workers {shlex.quote(str(args.num_workers))} "
        f"--output-root {shlex.quote(CONTAINER_OUTPUT_ROOT)} "
        f"--run-id {shlex.quote(args.run_id)} "
        f"--seed {shlex.quote(str(args.seed))}; "
        'status=$?; chown -R "$HOST_UID:$HOST_GID" /workspace/output || true; exit $status'
    )

    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--gpus",
        f"device={gpu_index}",
        "--shm-size=2g",
        "--cap-add=SYS_PTRACE",
        "--security-opt",
        "seccomp=unconfined",
        "-e",
        f"HOST_UID={os.getuid()}",
        "-e",
        f"HOST_GID={os.getgid()}",
        "-e",
        f"PS2ENV_HOST_UID={os.getuid()}",
        "-e",
        f"PS2ENV_HOST_GID={os.getgid()}",
        "-e",
        "PS2ENV_INSTALL_NVIDIA_DISPLAY_DRIVER=1",
        "-e",
        f"PS2ENV_CACHE_DIR={CONTAINER_CACHE_ROOT}",
        "-v",
        f"{outdir}:{CONTAINER_OUTPUT_ROOT}",
        "-v",
        f"{cache_dir}:{CONTAINER_CACHE_ROOT}",
        "--entrypoint",
        "bash",
        args.image,
        "-lc",
        runtime_cmd,
    ]

    logger.info("Docker command: %s", shlex.join(docker_cmd))
    with container_log_path.open("w", encoding="utf-8") as handle:
        result = subprocess.run(docker_cmd, stdout=handle, stderr=subprocess.STDOUT, check=False)

    if result.returncode != 0:
        logger.error("Container failed with exit code %s. See %s", result.returncode, container_log_path)
        return int(result.returncode)

    logger.info("Container finished successfully. Artifacts written to %s", outdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
