from __future__ import annotations

import os
import random
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .capture import FfmpegCapture, capture_single_frame
from .config import SmokeConfig
from .gpu import GpuAdapter
from .input import SMOKE_ACTION_KEYS, random_smoke_action, send_hotkey, send_key_tap
from .logging_utils import JsonEventLogger, configure_worker_logger
from .pcsx2 import (
    build_launch_command,
    build_worker_environment,
    select_bios_file,
    stage_worker_pcsx2_tree,
    write_worker_settings,
)
from .pine import pine_socket_path, wait_for_pine_socket
from .xdummy import XDummyServer


def _terminate_process(proc: subprocess.Popen[bytes] | None, timeout: float = 10.0) -> None:
    if not proc or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _wait_for_window(display: str, proc: subprocess.Popen[bytes], timeout_seconds: float) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"PCSX2 exited before creating a render window (exit code {proc.returncode})")
        result = subprocess.run(
            ["xdotool", "search", "--pid", str(proc.pid), "--all", "--name", ".*"],
            capture_output=True,
            text=True,
            env={"DISPLAY": display},
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in reversed(result.stdout.splitlines()):
                line = line.strip()
                if line:
                    return int(line, 10)
        time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for render window for process {proc.pid} on {display}")


def _prepare_window(display: str, window_id: int, width: int, height: int) -> None:
    env = {"DISPLAY": display}
    subprocess.run(
        ["xdotool", "windowmove", str(window_id), "0", "0"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    subprocess.run(
        ["xdotool", "windowsize", str(window_id), str(width), str(height)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


@dataclass(frozen=True)
class WorkerContext:
    worker_id: int
    run_id: str
    output_root: Path
    config: SmokeConfig
    adapter: GpuAdapter
    pcsx2_source_root: Path = Path("/opt/pcsx2")


def run_worker(ctx: WorkerContext) -> int:
    worker_name = f"worker-{ctx.worker_id:02d}"
    worker_root = ctx.output_root / ctx.run_id / worker_name
    worker_root.mkdir(parents=True, exist_ok=True)
    logger = configure_worker_logger(worker_name, worker_root / "worker.log", ctx.config.logging.level)
    events = JsonEventLogger(worker_name, ctx.worker_id, worker_root / "events.jsonl")

    xdg_runtime_dir = Path("/tmp/ps2env") / ctx.run_id[:16] / f"w{ctx.worker_id:02d}"
    xdg_runtime_dir.mkdir(parents=True, exist_ok=True)
    home_dir = worker_root / "home"
    home_dir.mkdir(parents=True, exist_ok=True)

    display_number = ctx.config.workers.display_base + ctx.worker_id
    display = f":{display_number}"
    pine_slot = ctx.config.workers.pine_slot_base + ctx.worker_id
    pcsx2_log_path = worker_root / "pcsx2.log"
    pcsx2_console_path = worker_root / "pcsx2-console.log"
    smoke_video_path = worker_root / "smoke.mp4"
    last_frame_path = worker_root / "last_frame.png"

    xorg = XDummyServer(
        display_number=display_number,
        width=ctx.config.capture.width,
        height=ctx.config.capture.height,
        config_path=worker_root / "xorg-dummy.conf",
        log_path=worker_root / "xorg.log",
    )
    pcsx2_process: subprocess.Popen[bytes] | None = None
    capture: FfmpegCapture | None = None

    try:
        logger.info("Starting worker runtime in %s", worker_root)
        events.emit("worker_start", pid=os.getpid(), runtime_dir=str(worker_root))

        logger.info(
            "Selecting round-robin GPU %s (Vulkan GPU %s) using adapter '%s'",
            ctx.adapter.ordinal_index,
            ctx.adapter.vulkan_index,
            ctx.adapter.adapter_name,
        )
        events.emit(
            "gpu_selected",
            gpu_ordinal=ctx.adapter.ordinal_index,
            vulkan_gpu_index=ctx.adapter.vulkan_index,
            device_name=ctx.adapter.device_name,
            adapter_name=ctx.adapter.adapter_name,
            device_uuid=ctx.adapter.device_uuid,
            display=display,
            pine_slot=pine_slot,
        )

        bios_path = select_bios_file(Path(ctx.config.game.bios_dir), ctx.config.game.bios_file)
        logger.info("Using BIOS file %s", bios_path.name)

        pcsx2_layout = stage_worker_pcsx2_tree(ctx.pcsx2_source_root, xdg_runtime_dir / "pcsx2-app")
        write_worker_settings(
            pcsx2_layout,
            ctx.config,
            bios_file=bios_path,
            adapter_name=ctx.adapter.adapter_name,
            pine_slot=pine_slot,
            pcsx2_log_path=pcsx2_log_path,
        )

        xorg.start()
        xorg.wait_until_ready(timeout_seconds=20.0)
        logger.info("Xdummy display %s is ready", display)
        events.emit("display_ready", display=display, xorg_log=str(xorg.log_path))

        pcsx2_env = build_worker_environment(
            pcsx2_layout,
            display=display,
            xdg_runtime_dir=xdg_runtime_dir,
            home_dir=home_dir,
        )
        launch_command = build_launch_command(
            pcsx2_layout,
            iso_path=ctx.config.game.iso_path,
            pcsx2_log_path=pcsx2_log_path,
            fastboot=ctx.config.game.fastboot,
        )
        with pcsx2_console_path.open("wb") as console_handle:
            pcsx2_process = subprocess.Popen(
                launch_command,
                cwd=pcsx2_layout.app_root,
                env=pcsx2_env,
                stdout=console_handle,
                stderr=subprocess.STDOUT,
            )

        logger.info("Launched PCSX2 pid=%s", pcsx2_process.pid)
        events.emit(
            "pcsx2_launch",
            pid=pcsx2_process.pid,
            command=launch_command,
            pcsx2_log=str(pcsx2_log_path),
            console_log=str(pcsx2_console_path),
        )

        socket_path = pine_socket_path(xdg_runtime_dir, pine_slot)
        wait_for_pine_socket(socket_path, timeout_seconds=60.0)
        logger.info("PINE socket is ready at %s", socket_path)
        events.emit("pine_ready", socket_path=str(socket_path))

        window_id = _wait_for_window(display, pcsx2_process, timeout_seconds=60.0)
        _prepare_window(display, window_id, ctx.config.capture.width, ctx.config.capture.height)
        logger.info("Render window %s is ready on %s", window_id, display)
        events.emit("window_ready", window_id=window_id, display=display)

        capture = FfmpegCapture(
            display=display,
            width=ctx.config.capture.width,
            height=ctx.config.capture.height,
            framerate=ctx.config.capture.framerate,
            output_path=smoke_video_path,
        )
        capture.start()
        logger.info("Started ffmpeg capture to %s", smoke_video_path)
        events.emit("capture_started", path=str(smoke_video_path))

        send_hotkey(display, window_id, ctx.config.input.pause_hotkey)
        logger.info("Sent unpause hotkey %s", ctx.config.input.pause_hotkey)
        events.emit("unpaused", hotkey=ctx.config.input.pause_hotkey)

        rng = random.Random(f"{ctx.run_id}:{ctx.worker_id}")
        deadline = time.monotonic() + ctx.config.workers.duration_seconds
        while time.monotonic() < deadline:
            if pcsx2_process.poll() is not None:
                raise RuntimeError(f"PCSX2 exited unexpectedly with code {pcsx2_process.returncode}")

            action = random_smoke_action(rng)
            key = SMOKE_ACTION_KEYS[action]
            started_at = time.monotonic()
            if key is not None:
                send_key_tap(display, window_id, key, ctx.config.input.press_duration_ms)
            events.emit("action_sent", action=action, key=key)

            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            remaining_ms = max(ctx.config.input.action_interval_ms - elapsed_ms, 0.0)
            time.sleep(remaining_ms / 1000.0)

        if capture:
            capture.stop()
        capture_single_frame(display, ctx.config.capture.width, ctx.config.capture.height, last_frame_path)
        logger.info("Captured last frame to %s", last_frame_path)

        _terminate_process(pcsx2_process)
        xorg.stop()
        events.emit("worker_exit", exit_code=0)
        logger.info("Worker finished successfully")
        return 0
    except Exception as exc:
        logger.exception("Worker failed: %s", exc)
        events.emit("worker_error", message=str(exc))
        try:
            if capture:
                capture.stop()
            if xorg.process and xorg.process.poll() is None:
                try:
                    capture_single_frame(display, ctx.config.capture.width, ctx.config.capture.height, last_frame_path)
                except Exception:
                    logger.warning("Failed to capture last frame during error cleanup", exc_info=True)
        finally:
            _terminate_process(pcsx2_process)
            xorg.stop()
        return 1
    finally:
        events.close()
