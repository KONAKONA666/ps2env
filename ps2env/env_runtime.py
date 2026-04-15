from __future__ import annotations

import argparse
import multiprocessing
import random
import sys
from pathlib import Path

from .config import apply_runtime_overrides, load_config
from .env import PS2Env
from .logging_utils import JsonEventLogger, configure_parent_logger, configure_worker_logger


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PS2Env lifecycle inside Docker.")
    parser.add_argument("--config", required=True, help="Path to the environment TOML config.")
    parser.add_argument("--workers", type=int, default=None, help="Override worker count.")
    parser.add_argument("--worker-id-base", type=int, default=0, help="Base worker id offset for this runtime instance.")
    parser.add_argument("--steps", type=int, default=8, help="Number of env.step() calls per worker.")
    parser.add_argument("--output-root", default="/workspace/output", help="Root directory for artifacts.")
    parser.add_argument("--run-id", default="env-run", help="Run identifier.")
    parser.add_argument("--game", default=None, help="Override ISO path.")
    parser.add_argument("--bios-dir", default=None, help="Override BIOS directory.")
    parser.add_argument("--seed", type=int, default=7, help="Base RNG seed.")
    return parser.parse_args()


def _run_env_worker(
    worker_id: int,
    config_path: str,
    output_root: str,
    run_id: str,
    steps: int,
    seed: int,
    game_override: str | None,
    bios_override: str | None,
) -> int:
    config = apply_runtime_overrides(
        load_config(config_path),
        workers=1,
        game_path=game_override,
        bios_dir=bios_override,
    )
    worker_root = Path(output_root) / run_id / f"worker-{worker_id:02d}"
    logger = configure_worker_logger(f"worker-{worker_id:02d}", worker_root / "worker.log", config.logging.level)
    events = JsonEventLogger(f"worker-{worker_id:02d}", worker_id, worker_root / "events.jsonl")
    rng = random.Random(seed + worker_id)
    env = PS2Env(config, worker_id=worker_id, output_root=output_root, run_id=run_id)

    try:
        events.emit("env_worker_start", steps=steps)
        start_info = env.start()
        events.emit("env_start_complete", info=start_info)
        observation, init_info = env.init()
        events.emit("env_init_complete", observation_shape=list(observation.shape), info=init_info)

        label_count = len(config.input.action_labels)
        for step_index in range(steps):
            action = rng.randrange(label_count) if label_count > 0 else 0
            observation, reward, terminated, truncated, info = env.step(action)
            events.emit(
                "env_step_complete",
                step_index=step_index,
                action=action,
                reward=reward,
                terminated=terminated,
                truncated=truncated,
                observation_shape=list(observation.shape),
                info=info,
            )
            logger.info(
                "Step %s action=%s reward=%s terminated=%s truncated=%s frame_count=%s",
                step_index,
                action,
                reward,
                terminated,
                truncated,
                info.get("frame_count"),
            )
            if terminated or truncated:
                observation, reset_info = env.reset()
                events.emit("env_reset_complete", observation_shape=list(observation.shape), info=reset_info)

        env.kill()
        events.emit("env_worker_exit", exit_code=0)
        return 0
    except Exception as exc:
        logger.exception("Environment worker failed: %s", exc)
        events.emit("env_worker_error", message=str(exc))
        try:
            env.kill()
        except Exception:
            pass
        return 1
    finally:
        events.close()


def main() -> int:
    args = _parse_args()
    base_config = load_config(args.config)
    config = apply_runtime_overrides(
        base_config,
        workers=args.workers,
        game_path=args.game,
        bios_dir=args.bios_dir,
    )
    logger = configure_parent_logger(config.logging.level)
    logger.info("Running PS2Env runtime from %s", args.config)

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    ctx = multiprocessing.get_context("spawn")
    processes: list[tuple[int, multiprocessing.Process]] = []
    for worker_offset in range(config.workers.count):
        worker_id = args.worker_id_base + worker_offset
        proc = ctx.Process(
            target=_process_main,
            args=(
                worker_id,
                args.config,
                str(output_root),
                args.run_id,
                args.steps,
                args.seed,
                args.game,
                args.bios_dir,
            ),
            name=f"env-worker-{worker_id:02d}",
        )
        proc.start()
        processes.append((worker_id, proc))
        logger.info("Started env worker-%02d", worker_id)

    exit_code = 0
    for worker_id, proc in processes:
        proc.join()
        if proc.exitcode != 0:
            exit_code = 1
            logger.error("env worker-%02d exited with code %s", worker_id, proc.exitcode)
        else:
            logger.info("env worker-%02d completed successfully", worker_id)
    return exit_code


def _process_main(
    worker_id: int,
    config_path: str,
    output_root: str,
    run_id: str,
    steps: int,
    seed: int,
    game_override: str | None,
    bios_override: str | None,
) -> None:
    code = _run_env_worker(
        worker_id,
        config_path,
        output_root,
        run_id,
        steps,
        seed,
        game_override,
        bios_override,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    sys.exit(main())
