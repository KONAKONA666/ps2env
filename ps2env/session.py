from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capture import FfmpegCapture, X11FrameCapture, capture_single_frame
from .config import PS2EnvConfig
from .gpu import GpuAdapter
from .input import X11InputController
from .pcsx2 import (
    WorkerPcsx2Layout,
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
    config: PS2EnvConfig
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
        self.layout: WorkerPcsx2Layout | None = None
        self.current_game_id: str | None = None
        self.current_game_crc: int | None = None
        self.paths = {
            "pcsx2_log": self.worker_root / "pcsx2.log",
            "pcsx2_console_log": self.worker_root / "pcsx2-console.log",
            "session_video": self.worker_root / "session.mp4",
            "last_frame": self.worker_root / "last_frame.png",
            "xorg_log": self.worker_root / "xorg.log",
            "xorg_config": self.worker_root / "xorg-dummy.conf",
            "episode_state_cache": self.worker_root / "baseline" / "episode_start.p2s",
        }

    def start(self) -> dict[str, Any]:
        bios_path = select_bios_file(Path(self.config.game.bios_dir), self.config.game.bios_file)
        layout = stage_worker_pcsx2_tree(self.pcsx2_source_root, self.xdg_runtime_dir / "pcsx2-app")
        self.layout = layout

        self._cache_episode_start_state()

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

        self.input = X11InputController(self.display)

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
        self.pine.connect()

        self.window_id = self._wait_for_window(timeout_seconds=60.0)
        self.input.bind_window(self.window_id)
        self.input.move_resize_window(0, 0, self.config.capture.width, self.config.capture.height)
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
            output_path=self.paths["session_video"],
        )
        self.capture_recorder.start()
        self._wait_for_vm_ready(timeout_seconds=15.0)
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

        if self.pine is not None:
            try:
                self.pine.close()
            except Exception:
                pass
            self.pine = None

        if self.input is not None:
            try:
                self.input.close()
            except Exception:
                pass
            self.input = None

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

        if self.layout is not None:
            shutil.rmtree(self.layout.root, ignore_errors=True)
            self.layout = None

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
        if self.pine is None:
            raise RuntimeError("PINE client is not initialized.")
        if self.get_status() == PINEStatus.PAUSED:
            return
        self.pine.pause()
        self._wait_for_status(PINEStatus.PAUSED, timeout_seconds=5.0)

    def save_state_slot(self, slot: int) -> None:
        if self.pine is None:
            raise RuntimeError("PINE client is not initialized.")
        self.ensure_paused()
        self.pine.save_state_slot(slot)

    def load_state_slot(self, slot: int) -> None:
        if self.pine is None:
            raise RuntimeError("PINE client is not initialized.")
        self.ensure_paused()
        self.pine.load_state_slot(slot)
        self._wait_for_status(PINEStatus.PAUSED, timeout_seconds=10.0)

    def restore_episode_start_state(self) -> dict[str, Any] | None:
        if self.config.savestates.episode_start_file is None:
            return None
        self._cache_episode_start_state()
        game_id, game_crc = self._ensure_game_identity(timeout_seconds=30.0)
        target = self._slot_state_path(self.config.savestates.episode_start_slot, game_id=game_id, game_crc=game_crc)
        shutil.copy2(self.paths["episode_state_cache"], target)
        try:
            self.load_state_slot(self.config.savestates.episode_start_slot)
            seeded_from_current_state = False
        except Exception:
            self._seed_episode_start_state_from_current_vm(target, slot=self.config.savestates.episode_start_slot)
            self.load_state_slot(self.config.savestates.episode_start_slot)
            seeded_from_current_state = True
        return {
            "game_id": game_id,
            "game_crc": f"{game_crc:08X}",
            "slot": self.config.savestates.episode_start_slot,
            "source": str(self.paths["episode_state_cache"]),
            "target": str(target),
            "seeded_from_current_state": seeded_from_current_state,
        }

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
        if self.pine is None:
            raise RuntimeError("PINE client is not initialized.")

        total_start = time.monotonic()
        self.ensure_paused()
        self.pine.frame_advance(frame_count)
        transition = self._wait_for_frame_advance(timeout_seconds=5.0)
        self.frame_count += frame_count
        return {
            "requested_frames": frame_count,
            "advanced_frames": frame_count,
            "frame_transitions_observed": 1 if transition["observed_running"] else 0,
            "status_polls": transition["polls"],
            "total_ms": (time.monotonic() - total_start) * 1000.0,
        }

    def _wait_for_frame_advance(self, *, timeout_seconds: float) -> dict[str, Any]:
        observed_running = False
        polls = 0
        deadline = time.monotonic() + timeout_seconds
        saw_initial_pause = False
        while time.monotonic() < deadline:
            if not self.is_game_alive():
                raise RuntimeError("PCSX2 exited while advancing frames.")
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
            time.sleep(0.01)
        raise TimeoutError(f"Timed out waiting for PCSX2 status '{expected_status}'.")

    def _wait_for_vm_ready(self, *, timeout_seconds: float) -> str:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not self.is_game_alive():
                raise RuntimeError("PCSX2 exited unexpectedly while waiting for VM initialization.")
            status = self.get_status()
            if status in (PINEStatus.PAUSED, PINEStatus.RUNNING):
                return status
            time.sleep(0.05)
        raise TimeoutError("Timed out waiting for PCSX2 VM to reach a runnable state.")

    def _wait_for_window(self, *, timeout_seconds: float) -> int:
        if self.pcsx2_process is None:
            raise RuntimeError("PCSX2 process is not running.")
        if self.input is None:
            raise RuntimeError("Input controller is not initialized.")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.pcsx2_process.poll() is not None:
                raise RuntimeError(
                    f"PCSX2 exited before creating a render window (exit code {self.pcsx2_process.returncode})"
                )
            window_id = self.input.find_window_by_pid(self.pcsx2_process.pid)
            if window_id is not None:
                return window_id
            time.sleep(0.25)
        raise TimeoutError(f"Timed out waiting for render window for process {self.pcsx2_process.pid} on {self.display}")

    def _cache_episode_start_state(self) -> None:
        source = self.config.savestates.episode_start_file
        if source is None:
            return
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(f"Configured episode_start_file does not exist: {source_path}")
        cache_path = self.paths["episode_state_cache"]
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists():
            return
        shutil.copy2(source_path, cache_path)

    def _ensure_game_identity(self, *, timeout_seconds: float) -> tuple[str, int]:
        if self.current_game_id and self.current_game_crc is not None:
            return self.current_game_id, self.current_game_crc
        if self.pine is None:
            raise RuntimeError("PINE client is not initialized.")
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if not self.is_game_alive():
                raise RuntimeError("PCSX2 exited unexpectedly while reading game metadata.")
            try:
                game_id = self.pine.get_game_id().strip()
                game_crc = self.pine.get_game_crc()
                if game_id:
                    self.current_game_id = game_id
                    self.current_game_crc = game_crc
                    return game_id, game_crc
            except Exception as exc:
                last_error = exc
            time.sleep(0.25)
        detail = f" Last error: {last_error}" if last_error is not None else ""
        raise TimeoutError(f"Timed out waiting for PCSX2 game metadata.{detail}")

    def _slot_state_path(self, slot: int, *, game_id: str, game_crc: int) -> Path:
        if self.layout is None:
            raise RuntimeError("PCSX2 layout is not initialized.")
        sstates_dir = self.layout.app_root / "sstates"
        sstates_dir.mkdir(parents=True, exist_ok=True)
        return sstates_dir / f"{game_id} ({game_crc:08X}).{slot:02d}.p2s"

    def _seed_episode_start_state_from_current_vm(self, target: Path, *, slot: int) -> None:
        self.save_state_slot(slot)
        if not target.is_file():
            raise FileNotFoundError(f"Expected savestate slot file was not written: {target}")
        shutil.copy2(target, self.paths["episode_state_cache"])
