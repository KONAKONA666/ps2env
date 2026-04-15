from __future__ import annotations

import argparse
import multiprocessing
import sys
from pathlib import Path

from .config import apply_runtime_overrides, load_config
from .gpu import GpuAdapter, discover_discrete_nvidia_adapters
from .logging_utils import configure_parent_logger
from .worker import WorkerContext, run_worker


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the PS2Env smoke-runtime inside Docker.")
    parser.add_argument("--config", required=True, help="Path to the smoke TOML config.")
    parser.add_argument("--workers", type=int, default=None, help="Override worker count.")
    parser.add_argument("--duration-seconds", type=int, default=None, help="Override smoke run duration.")
    parser.add_argument("--output-root", default="/workspace/output", help="Root directory for artifacts.")
    parser.add_argument("--run-id", default=None, help="Run identifier used under the output root.")
    parser.add_argument("--worker-id-base", type=int, default=0, help="Base worker id offset for this runtime instance.")
    parser.add_argument("--game", default=None, help="Override ISO path.")
    parser.add_argument("--bios-dir", default=None, help="Override BIOS directory.")
    return parser.parse_args()


def _worker_entry(worker_id: int, run_id: str, output_root: str, config_dict: dict[str, object], adapter: GpuAdapter) -> int:
    config = config_dict["config"]
    ctx = WorkerContext(
        worker_id=worker_id,
        run_id=run_id,
        output_root=Path(output_root),
        config=config,
        adapter=adapter,
    )
    return run_worker(ctx)


def main() -> int:
    args = _parse_args()
    base_config = load_config(args.config)
    config = apply_runtime_overrides(
        base_config,
        workers=args.workers,
        duration_seconds=args.duration_seconds,
        game_path=args.game,
        bios_dir=args.bios_dir,
    )
    run_id = args.run_id or "run-local"

    logger = configure_parent_logger(config.logging.level)
    logger.info("Loading smoke runtime config from %s", args.config)

    adapters = discover_discrete_nvidia_adapters()
    logger.info("Discovered %s discrete NVIDIA Vulkan adapter(s)", len(adapters))
    for adapter in adapters:
        logger.info(
            "Adapter %s => round-robin GPU %s, Vulkan GPU %s (%s)",
            adapter.adapter_name,
            adapter.ordinal_index,
            adapter.vulkan_index,
            adapter.device_uuid or "no-uuid",
        )

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    logger.info("Artifacts will be written under %s/%s", output_root, run_id)

    processes: list[tuple[int, multiprocessing.Process]] = []
    exit_code = 0
    ctx = multiprocessing.get_context("spawn")
    payload = {"config": config}

    try:
        for worker_id in range(config.workers.count):
            global_worker_id = args.worker_id_base + worker_id
            adapter = adapters[worker_id % len(adapters)]
            proc = ctx.Process(
                target=_process_main,
                args=(global_worker_id, run_id, str(output_root), payload, adapter),
                name=f"worker-{global_worker_id:02d}",
            )
            proc.start()
            processes.append((global_worker_id, proc))
            logger.info(
                "Started worker-%02d on adapter '%s' (round-robin GPU %s, Vulkan GPU %s)",
                global_worker_id,
                adapter.adapter_name,
                adapter.ordinal_index,
                adapter.vulkan_index,
            )

        for worker_id, proc in processes:
            proc.join()
            if proc.exitcode != 0:
                exit_code = 1
                logger.error("worker-%02d exited with code %s", worker_id, proc.exitcode)
            else:
                logger.info("worker-%02d completed successfully", worker_id)
    except KeyboardInterrupt:
        exit_code = 1
        logger.warning("Interrupted, terminating worker processes")
        for _, proc in processes:
            if proc.is_alive():
                proc.terminate()
        for _, proc in processes:
            proc.join(timeout=5)
    return exit_code


def _process_main(worker_id: int, run_id: str, output_root: str, payload: dict[str, object], adapter: GpuAdapter) -> None:
    config = payload["config"]
    code = _worker_entry(worker_id, run_id, output_root, {"config": config}, adapter)
    raise SystemExit(code)


if __name__ == "__main__":
    sys.exit(main())
