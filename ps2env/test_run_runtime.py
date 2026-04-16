from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import PS2EnvConfig, apply_runtime_overrides, load_config
from .env import PS2Env
from .logging_utils import JsonEventLogger, configure_parent_logger, configure_worker_logger
from .test_run_common import parse_actions_literal


EnvFactory = Callable[..., PS2Env]


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return repr(value)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * fraction
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)


def _summarize_result(result: Any) -> dict[str, Any] | None:
    if isinstance(result, dict):
        return _json_safe(result)
    if isinstance(result, tuple):
        if len(result) == 2 and isinstance(result[1], dict):
            return {"info": _json_safe(result[1])}
        if len(result) == 5 and isinstance(result[4], dict):
            return {
                "reward": float(result[1]),
                "terminated": bool(result[2]),
                "truncated": bool(result[3]),
                "info": _json_safe(result[4]),
            }
    return None


@dataclass
class TimingLogger:
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")

    def emit(self, **fields: Any) -> None:
        self._handle.write(json.dumps(_json_safe(fields), sort_keys=True) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


@dataclass
class WorkerSummary:
    worker_id: int
    worker_name: str
    status: str = "pending"
    completed_steps: int = 0
    starts: int = 0
    inits: int = 0
    resets: int = 0
    restarts: int = 0
    last_error: str | None = None


class SummaryCollector:
    def __init__(self, num_workers: int, actions: tuple[Any, ...], num_steps: int) -> None:
        self._lock = threading.Lock()
        self._timings: list[dict[str, Any]] = []
        self._workers: dict[int, WorkerSummary] = {
            worker_id: WorkerSummary(worker_id=worker_id, worker_name=f"worker-{worker_id:02d}")
            for worker_id in range(num_workers)
        }
        self.actions = tuple(actions)
        self.num_steps = int(num_steps)
        self.had_failure = False

    def record_timing(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._timings.append(dict(record))
            if not bool(record.get("success", False)):
                self.had_failure = True

    def bump(self, worker_id: int, field: str, amount: int = 1) -> None:
        with self._lock:
            current = getattr(self._workers[worker_id], field)
            setattr(self._workers[worker_id], field, int(current) + int(amount))

    def set_completed_steps(self, worker_id: int, value: int) -> None:
        with self._lock:
            self._workers[worker_id].completed_steps = int(value)

    def set_status(self, worker_id: int, status: str, *, error: str | None = None) -> None:
        with self._lock:
            worker = self._workers[worker_id]
            worker.status = status
            if error is not None:
                worker.last_error = error
                self.had_failure = True

    def summary(self, *, run_id: str) -> dict[str, Any]:
        with self._lock:
            timings = list(self._timings)
            workers = [self._workers[index] for index in sorted(self._workers)]

        operations: dict[str, dict[str, Any]] = {}
        for record in timings:
            operation = str(record["operation"])
            bucket = operations.setdefault(
                operation,
                {
                    "count": 0,
                    "success_count": 0,
                    "failure_count": 0,
                    "durations_ms": [],
                },
            )
            bucket["count"] += 1
            if bool(record.get("success", False)):
                bucket["success_count"] += 1
            else:
                bucket["failure_count"] += 1
            bucket["durations_ms"].append(float(record["duration_ms"]))

        for bucket in operations.values():
            durations = list(bucket.pop("durations_ms"))
            bucket["min_ms"] = min(durations) if durations else None
            bucket["mean_ms"] = (sum(durations) / len(durations)) if durations else None
            bucket["max_ms"] = max(durations) if durations else None
            bucket["p95_ms"] = _percentile(durations, 0.95)

        return {
            "timestamp": _utcnow(),
            "run_id": run_id,
            "num_steps": self.num_steps,
            "actions": _json_safe(self.actions),
            "overall_success": not self.had_failure and all(worker.status == "completed" for worker in workers),
            "total_workers": len(workers),
            "totals": {
                "steps": sum(worker.completed_steps for worker in workers),
                "starts": sum(worker.starts for worker in workers),
                "inits": sum(worker.inits for worker in workers),
                "resets": sum(worker.resets for worker in workers),
                "restarts": sum(worker.restarts for worker in workers),
            },
            "operations": operations,
            "workers": [
                {
                    "worker_id": worker.worker_id,
                    "worker_name": worker.worker_name,
                    "status": worker.status,
                    "completed_steps": worker.completed_steps,
                    "starts": worker.starts,
                    "inits": worker.inits,
                    "resets": worker.resets,
                    "restarts": worker.restarts,
                    "last_error": worker.last_error,
                }
                for worker in workers
            ],
        }


class BootstrapCoordinator:
    def __init__(self, total_workers: int) -> None:
        self.total_workers = int(total_workers)
        self._lock = threading.Lock()
        self._successful_starts = 0
        self._released = False
        self.event = threading.Event()

    def note_success(self) -> None:
        with self._lock:
            self._successful_starts += 1
            if self._successful_starts >= self.total_workers:
                self._released = True
                self.event.set()

    def release_failure(self) -> None:
        with self._lock:
            self._released = True
            self.event.set()

    @property
    def successful_starts(self) -> int:
        with self._lock:
            return self._successful_starts

    @property
    def released(self) -> bool:
        with self._lock:
            return self._released


@dataclass
class WorkerContext:
    worker_id: int
    worker_name: str
    worker_root: Path
    config: PS2EnvConfig
    actions: tuple[Any, ...]
    num_steps: int
    reset_steps: int
    restart_steps: int
    runtime_root: Path
    run_id: str
    seed: int
    start_gate: threading.Semaphore
    bootstrap: BootstrapCoordinator
    stop_event: threading.Event
    summary: SummaryCollector
    env_factory: EnvFactory


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run asynchronous PS2Env lifecycle testing inside Docker.")
    parser.add_argument("--config", required=True, help="Path to the environment TOML config.")
    parser.add_argument("--actions", required=True, help="Python literal describing the action pool.")
    parser.add_argument("--num-steps", type=int, required=True, help="Completed step() calls per worker.")
    parser.add_argument("--reset-steps", type=int, required=True, help="Reset every N completed steps per worker. <=0 disables.")
    parser.add_argument("--restart-steps", type=int, required=True, help="Restart every N completed steps per worker. <=0 disables.")
    parser.add_argument("--n-parallel-starts", type=int, required=True, help="Maximum concurrent start() calls.")
    parser.add_argument("--num-workers", type=int, required=True, help="Number of worker envs to run.")
    parser.add_argument("--output-root", required=True, help="Mounted output directory inside the container.")
    parser.add_argument("--run-id", required=True, help="Internal run identifier.")
    parser.add_argument("--seed", type=int, default=7, help="Base RNG seed.")
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> tuple[Any, ...]:
    actions = parse_actions_literal(args.actions)
    if args.num_steps < 1:
        raise ValueError("--num-steps must be >= 1.")
    if args.num_workers < 1:
        raise ValueError("--num-workers must be >= 1.")
    if args.n_parallel_starts < 1:
        raise ValueError("--n-parallel-starts must be >= 1.")
    return actions


def _acquire_start_slot(ctx: WorkerContext) -> bool:
    while not ctx.stop_event.is_set():
        if ctx.start_gate.acquire(timeout=0.25):
            return True
    return False


def _record_operation(
    logger: Any,
    events: JsonEventLogger,
    timings: TimingLogger,
    summary: SummaryCollector,
    worker_id: int,
    operation: str,
    func: Callable[[], Any],
    *,
    step_index: int | None = None,
    action: Any = None,
    extra: dict[str, Any] | None = None,
) -> Any:
    meta: dict[str, Any] = {
        "operation": operation,
        "step_index": step_index,
        "action": _json_safe(action),
    }
    if extra:
        meta.update(_json_safe(extra))

    started_at = _utcnow()
    started = time.monotonic()
    events.emit("operation_start", **meta)
    try:
        result = func()
    except Exception as exc:
        finished_at = _utcnow()
        duration_ms = (time.monotonic() - started) * 1000.0
        record = {
            **meta,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "success": False,
            "error": str(exc),
        }
        timings.emit(**record)
        summary.record_timing(record)
        events.emit("operation_end", **record)
        logger.exception("%s failed after %.2f ms", operation, duration_ms)
        raise

    finished_at = _utcnow()
    duration_ms = (time.monotonic() - started) * 1000.0
    record = {
        **meta,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "success": True,
    }
    result_summary = _summarize_result(result)
    if result_summary is not None:
        record["result"] = result_summary
    timings.emit(**record)
    summary.record_timing(record)
    events.emit("operation_end", **record)
    logger.info("%s completed in %.2f ms", operation, duration_ms)
    return result


def _bootstrap_start(ctx: WorkerContext, env: PS2Env, logger: Any, events: JsonEventLogger, timings: TimingLogger) -> bool:
    if not _acquire_start_slot(ctx):
        return False
    try:
        _record_operation(
            logger,
            events,
            timings,
            ctx.summary,
            ctx.worker_id,
            "start",
            env.start,
            extra={"phase": "bootstrap"},
        )
    finally:
        ctx.start_gate.release()

    ctx.summary.bump(ctx.worker_id, "starts")
    ctx.bootstrap.note_success()
    return True


def _restart_worker(ctx: WorkerContext, env: PS2Env, logger: Any, events: JsonEventLogger, timings: TimingLogger, *, step_index: int) -> None:
    def _cycle() -> None:
        _record_operation(
            logger,
            events,
            timings,
            ctx.summary,
            ctx.worker_id,
            "kill",
            env.kill,
            step_index=step_index,
            extra={"reason": "scheduled_restart"},
        )

        if not _acquire_start_slot(ctx):
            raise RuntimeError("Restart aborted while waiting for an available start slot.")
        try:
            _record_operation(
                logger,
                events,
                timings,
                ctx.summary,
                ctx.worker_id,
                "start",
                env.start,
                step_index=step_index,
                extra={"phase": "restart"},
            )
        finally:
            ctx.start_gate.release()

        _record_operation(
            logger,
            events,
            timings,
            ctx.summary,
            ctx.worker_id,
            "init",
            env.init,
            step_index=step_index,
            extra={"phase": "restart"},
        )

    _record_operation(
        logger,
        events,
        timings,
        ctx.summary,
        ctx.worker_id,
        "restart_cycle",
        _cycle,
        step_index=step_index,
    )
    ctx.summary.bump(ctx.worker_id, "restarts")
    ctx.summary.bump(ctx.worker_id, "starts")
    ctx.summary.bump(ctx.worker_id, "inits")


def _reset_worker(
    ctx: WorkerContext,
    env: PS2Env,
    logger: Any,
    events: JsonEventLogger,
    timings: TimingLogger,
    *,
    step_index: int,
    reason: str,
) -> None:
    _record_operation(
        logger,
        events,
        timings,
        ctx.summary,
        ctx.worker_id,
        "reset",
        env.reset,
        step_index=step_index,
        extra={"reason": reason},
    )
    ctx.summary.bump(ctx.worker_id, "resets")


def _worker_main(ctx: WorkerContext) -> None:
    logger = configure_worker_logger(f"{ctx.run_id}-{ctx.worker_name}", ctx.worker_root / "worker.log", ctx.config.logging.level)
    events = JsonEventLogger(ctx.worker_name, ctx.worker_id, ctx.worker_root / "events.jsonl")
    timings = TimingLogger(ctx.worker_root / "timings.jsonl")
    rng = random.Random(ctx.seed + ctx.worker_id)
    env = ctx.env_factory(ctx.config, worker_id=ctx.worker_id, output_root=ctx.runtime_root, run_id=ctx.run_id)

    try:
        if ctx.stop_event.is_set():
            ctx.summary.set_status(ctx.worker_id, "aborted")
            return

        if not _bootstrap_start(ctx, env, logger, events, timings):
            ctx.summary.set_status(ctx.worker_id, "aborted")
            return

        logger.info(
            "Waiting for bootstrap barrier: %s/%s workers started",
            ctx.bootstrap.successful_starts,
            ctx.bootstrap.total_workers,
        )
        ctx.bootstrap.event.wait()
        if ctx.stop_event.is_set():
            ctx.summary.set_status(ctx.worker_id, "aborted")
            return

        _record_operation(
            logger,
            events,
            timings,
            ctx.summary,
            ctx.worker_id,
            "init",
            env.init,
            extra={"phase": "bootstrap"},
        )
        ctx.summary.bump(ctx.worker_id, "inits")

        completed_steps = 0
        while not ctx.stop_event.is_set() and completed_steps < ctx.num_steps:
            action = rng.choice(ctx.actions)
            observation, reward, terminated, truncated, info = _record_operation(
                logger,
                events,
                timings,
                ctx.summary,
                ctx.worker_id,
                "step",
                lambda: env.step(action),
                step_index=completed_steps,
                action=action,
            )

            del observation, reward  # The test runner tracks timing/artifacts, not observations.
            completed_steps += 1
            ctx.summary.set_completed_steps(ctx.worker_id, completed_steps)
            logger.info(
                "Step %s/%s action=%s terminated=%s truncated=%s frame_count=%s",
                completed_steps,
                ctx.num_steps,
                action,
                terminated,
                truncated,
                info.get("frame_count"),
            )

            if completed_steps >= ctx.num_steps or ctx.stop_event.is_set():
                break

            restart_due = ctx.restart_steps > 0 and completed_steps % ctx.restart_steps == 0
            reset_due = (not restart_due) and ctx.reset_steps > 0 and completed_steps % ctx.reset_steps == 0

            if restart_due:
                _restart_worker(ctx, env, logger, events, timings, step_index=completed_steps)
                continue

            if reset_due:
                _reset_worker(ctx, env, logger, events, timings, step_index=completed_steps, reason="scheduled_reset")
                continue

            if terminated or truncated:
                _reset_worker(
                    ctx,
                    env,
                    logger,
                    events,
                    timings,
                    step_index=completed_steps,
                    reason="step_terminated" if terminated else "step_truncated",
                )

        final_status = "completed" if not ctx.stop_event.is_set() and completed_steps >= ctx.num_steps else "aborted"
        ctx.summary.set_status(ctx.worker_id, final_status)
        events.emit("worker_complete", completed_steps=completed_steps, status=final_status)
    except Exception as exc:
        ctx.summary.set_status(ctx.worker_id, "failed", error=str(exc))
        ctx.stop_event.set()
        ctx.bootstrap.release_failure()
        events.emit("worker_error", message=str(exc))
    finally:
        try:
            env.kill()
        except Exception as exc:
            logger.warning("cleanup kill failed: %s", exc, exc_info=True)
        events.close()
        timings.close()


def _move_worker_artifacts(output_root: Path, runtime_root: Path, run_id: str, num_workers: int) -> None:
    runtime_run_root = runtime_root / run_id
    for worker_id in range(num_workers):
        worker_name = f"worker-{worker_id:02d}"
        source = runtime_run_root / worker_name
        target = output_root / worker_name
        if not source.exists():
            continue
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(source), str(target))
    shutil.rmtree(runtime_root, ignore_errors=True)


def run_test(args: argparse.Namespace, *, env_factory: EnvFactory = PS2Env) -> int:
    actions = _validate_args(args)
    config = apply_runtime_overrides(load_config(args.config), workers=1)
    logger = configure_parent_logger(config.logging.level)

    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    runtime_root = output_root / ".runtime"
    shutil.rmtree(runtime_root, ignore_errors=True)
    runtime_run_root = runtime_root / args.run_id
    runtime_run_root.mkdir(parents=True, exist_ok=True)

    logger.info("Running threaded PS2Env test runtime from %s", args.config)
    logger.info(
        "Workers=%s num_steps=%s reset_steps=%s restart_steps=%s n_parallel_starts=%s",
        args.num_workers,
        args.num_steps,
        args.reset_steps,
        args.restart_steps,
        args.n_parallel_starts,
    )

    summary = SummaryCollector(args.num_workers, actions, args.num_steps)
    bootstrap = BootstrapCoordinator(args.num_workers)
    stop_event = threading.Event()
    start_gate = threading.Semaphore(min(args.n_parallel_starts, args.num_workers))

    threads: list[threading.Thread] = []
    for worker_id in range(args.num_workers):
        worker_name = f"worker-{worker_id:02d}"
        worker_root = runtime_run_root / worker_name
        worker_root.mkdir(parents=True, exist_ok=True)
        ctx = WorkerContext(
            worker_id=worker_id,
            worker_name=worker_name,
            worker_root=worker_root,
            config=config,
            actions=actions,
            num_steps=args.num_steps,
            reset_steps=args.reset_steps,
            restart_steps=args.restart_steps,
            runtime_root=runtime_root,
            run_id=args.run_id,
            seed=args.seed,
            start_gate=start_gate,
            bootstrap=bootstrap,
            stop_event=stop_event,
            summary=summary,
            env_factory=env_factory,
        )
        thread = threading.Thread(target=_worker_main, args=(ctx,), name=worker_name)
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

    _move_worker_artifacts(output_root, runtime_root, args.run_id, args.num_workers)

    result = summary.summary(run_id=args.run_id)
    summary_path = output_root / "summary.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return 0 if bool(result["overall_success"]) else 1


def main() -> int:
    return run_test(_parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
