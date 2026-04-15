from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capture import FfmpegCapture, X11FrameCapture, capture_single_frame
from .config import SmokeConfig
from .gpu import GpuAdapter
from .input import X11InputController
from .pcsx2 import (
    build_launch_command,
    build_worker_environment,
    select_bios_file,
    stage_worker_pcsx2_tree,
    write_worker_settings,
)
from .pine import PINEStatus, PineClient, pine_socket_path, wait_for_pine_socket
from .xdummy import XDummyServer


@dataclass
class PCSX2Session:
    config: SmokeConfig
    worker_id: int
    run_id: str
    output_root: Path
    adapter: GpuAdapter
    pcsx2_source_root: Path = Path("/opt/pcsx2")

    def __post_init__(self) -> None:
        self.worker_name = f"worker-{self.worker_id:02d}"
        self.worker_root = self.output_root / self.run_id / self.worker_name
        self.worker_root.mkdir(parents=True, exist_ok=True)
        self.display_number = self.config.workers.display_base + self.worker_id
        self.display = f":{self.display_number}"
        self.pine_slot = self.config.workers.pine_slot_base + self.worker_id
        self.xdg_runtime_dir = Path("/tmp/ps2env") / self.run_id[:16] / f"w{self.worker_id:02d}"
        self.xdg_runtime_dir.mkdir(parents=True, exist_ok=True)
        self.home_dir = self.worker_root / "home"
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.frame_count = 0
        self.xorg: XDummyServer | None = None
        self.pcsx2_process: subprocess.Popen[bytes] | None = None
        self.capture_recorder: FfmpegCapture | None = None
        self.frame_capture: X11FrameCapture | None = None
        self.input: X11InputController | None = None
        self.pine: PineClient | None = None
        self.window_id: int | None = None
        self.layout_root: Path | None = None
        self.paths = {
            "pcsx2_log": self.worker_root / "pcsx2.log",
            "pcsx2_console_log": self.worker_root / "pcsx2-console.log",
            "smoke_video": self.worker_root / "smoke.mp4",
            "last_frame": self.worker_root / "last_frame.png",
            "xorg_log": self.worker_root / "xorg.log",
            "xorg_config": self.worker_root / "xorg-dummy.conf",
        }

    def start(self) -> dict[str, Any]:
        bios_path = select_bios_file(Path(self.config.game.bios_dir), self.config.game.bios_file)
        layout = stage_worker_pcsx2_tree(self.pcsx2_source_root, self.xdg_runtime_dir / "pcsx2-app")
        self.layout_root = layout.root

        write_worker_settings(
            layout,
            self.config,
            bios_file=bios_path,
            adapter_name=self.adapter.adapter_name,
            pine_slot=self.pine_slot,
            pcsx2_log_path=self.paths["pcsx2_log"],
        )

        self.xorg = XDummyServer(
            display_number=self.display_number,
            width=self.config.capture.width,
            height=self.config.capture.height,
            config_path=self.paths["xorg_config"],
            log_path=self.paths["xorg_log"],
        )
        self.xorg.start()
        self.xorg.wait_until_ready(timeout_seconds=20.0)

        pcsx2_env = build_worker_environment(
            layout,
            display=self.display,
            xdg_runtime_dir=self.xdg_runtime_dir,
            home_dir=self.home_dir,
        )
        launch_command = build_launch_command(
            layout,
            iso_path=self.config.game.iso_path,
            pcsx2_log_path=self.paths["pcsx2_log"],
            fastboot=self.config.game.fastboot,
        )
        with self.paths["pcsx2_console_log"].open("wb") as console_handle:
            self.pcsx2_process = subprocess.Popen(
                launch_command,
                cwd=layout.app_root,
                env=pcsx2_env,
                stdout=console_handle,
                stderr=subprocess.STDOUT,
            )

        socket_path = pine_socket_path(self.xdg_runtime_dir, self.pine_slot)
        wait_for_pine_socket(socket_path, timeout_seconds=60.0)
        self.pine = PineClient(socket_path)

        self.window_id = self._wait_for_window(timeout_seconds=60.0)
        self._prepare_window(self.window_id)
        self.input = X11InputController(self.display, self.window_id)
        self.frame_capture = X11FrameCapture(
            display=self.display,
            width=self.config.capture.width,
            height=self.config.capture.height,
            observation_shape=self.config.capture.observation_shape,
        )
        self.capture_recorder = FfmpegCapture(
            display=self.display,
            width=self.config.capture.width,
            height=self.config.capture.height,
            framerate=self.config.capture.framerate,
            output_path=self.paths["smoke_video"],
        )
        self.capture_recorder.start()
        self.ensure_paused()

        return {
            "bios_file": bios_path.name,
            "display": self.display,
            "pine_slot": self.pine_slot,
            "window_id": self.window_id,
            "adapter_name": self.adapter.adapter_name,
        }

    def stop(self) -> None:
        if self.capture_recorder is not None:
            try:
                self.capture_recorder.stop()
            except Exception:
                pass
            self.capture_recorder = None

        try:
            if self.display and self.frame_capture is not None:
                capture_single_frame(
                    self.display,
                    self.config.capture.width,
                    self.config.capture.height,
                    self.paths["last_frame"],
                )
        except Exception:
            pass

        if self.input is not None:
            try:
                self.input.release_all()
            except Exception:
                pass

        if self.pcsx2_process is not None and self.pcsx2_process.poll() is None:
            self.pcsx2_process.terminate()
            try:
                self.pcsx2_process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                self.pcsx2_process.kill()
                self.pcsx2_process.wait(timeout=5.0)

        if self.xorg is not None:
            self.xorg.stop()
            self.xorg = None

        if self.layout_root is not None:
            shutil.rmtree(self.layout_root, ignore_errors=True)
            self.layout_root = None

    def is_game_alive(self) -> bool:
        return self.pcsx2_process is not None and self.pcsx2_process.poll() is None

    def get_pid(self) -> int | None:
        if self.pcsx2_process is None:
            return None
        return self.pcsx2_process.pid

    def get_status(self) -> str:
        if self.pine is None:
            return PINEStatus.SHUTDOWN
        return self.pine.get_status()

    def ensure_paused(self) -> None:
        status = self.get_status()
        if status == PINEStatus.PAUSED:
            return
        if self.input is None:
            raise RuntimeError("Input controller is not initialized.")
        self.input.tap_key(self.config.input.pause_hotkey)
        self._wait_for_status(PINEStatus.PAUSED, timeout_seconds=5.0)

    def capture_current_frame(self) -> tuple[Any, Any]:
        if self.frame_capture is None:
            raise RuntimeError("Frame capture is not initialized.")
        frame = self.frame_capture.grab_frame()
        observation = self.frame_capture.build_observation(frame)
        return frame, observation

    def save_debug_artifact(self, tag: str) -> str | None:
        output_path = self.worker_root / f"{tag}.png"
        try:
            capture_single_frame(
                self.display,
                self.config.capture.width,
                self.config.capture.height,
                output_path,
            )
            return str(output_path)
        except Exception:
            return None

    def advance_frames(self, frame_count: int) -> dict[str, Any]:
        if frame_count < 1:
            raise ValueError("frame_count must be >= 1")
        if self.input is None:
            raise RuntimeError("Input controller is not initialized.")

        profile: dict[str, Any] = {
            "requested_frames": frame_count,
            "advanced_frames": 0,
            "frame_transitions_observed": 0,
            "status_polls": 0,
            "total_ms": 0.0,
        }
        total_start = time.monotonic()
        self.ensure_paused()
        for _ in range(frame_count):
            transition = self._advance_one_frame()
            profile["advanced_frames"] += 1
            profile["status_polls"] += transition["polls"]
            if transition["observed_running"]:
                profile["frame_transitions_observed"] += 1
            self.frame_count += 1
        profile["total_ms"] = (time.monotonic() - total_start) * 1000.0
        return profile

    def _advance_one_frame(self) -> dict[str, Any]:
        if self.input is None:
            raise RuntimeError("Input controller is not initialized.")
        observed_running = False
        polls = 0
        self.input.tap_key(self.config.input.frame_advance_hotkey)
        deadline = time.monotonic() + 5.0
        saw_initial_pause = False
        while time.monotonic() < deadline:
            if not self.is_game_alive():
                raise RuntimeError("PCSX2 exited while advancing a frame.")
            polls += 1
            status = self.get_status()
            if status == PINEStatus.RUNNING:
                observed_running = True
            if status == PINEStatus.PAUSED:
                if observed_running or saw_initial_pause:
                    return {"observed_running": observed_running, "polls": polls}
                saw_initial_pause = True
            else:
                saw_initial_pause = False
        raise TimeoutError("Timed out waiting for PCSX2 to re-enter paused state after frame advance.")

    def _wait_for_status(self, expected_status: str, *, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not self.is_game_alive():
                raise RuntimeError("PCSX2 exited unexpectedly while waiting for status.")
            if self.get_status() == expected_status:
                return
        raise TimeoutError(f"Timed out waiting for PCSX2 status '{expected_status}'.")

    def _wait_for_window(self, *, timeout_seconds: float) -> int:
        if self.pcsx2_process is None:
            raise RuntimeError("PCSX2 process is not running.")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.pcsx2_process.poll() is not None:
                raise RuntimeError(
                    f"PCSX2 exited before creating a render window (exit code {self.pcsx2_process.returncode})"
                )
            result = subprocess.run(
                ["xdotool", "search", "--pid", str(self.pcsx2_process.pid), "--all", "--name", ".*"],
                capture_output=True,
                text=True,
                env={"DISPLAY": self.display},
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in reversed(result.stdout.splitlines()):
                    line = line.strip()
                    if line:
                        return int(line, 10)
            time.sleep(0.25)
        raise TimeoutError(f"Timed out waiting for render window for process {self.pcsx2_process.pid} on {self.display}")

    def _prepare_window(self, window_id: int) -> None:
        env = {"DISPLAY": self.display}
        subprocess.run(
            ["xdotool", "windowmove", str(window_id), "0", "0"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        subprocess.run(
            ["xdotool", "windowsize", str(window_id), str(self.config.capture.width), str(self.config.capture.height)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
