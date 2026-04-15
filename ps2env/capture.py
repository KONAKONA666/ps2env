from __future__ import annotations

import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class FfmpegCapture:
    display: str
    width: int
    height: int
    framerate: int
    output_path: Path
    process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "x11grab",
                "-draw_mouse",
                "0",
                "-video_size",
                f"{self.width}x{self.height}",
                "-framerate",
                str(self.framerate),
                "-i",
                f"{self.display}.0+0,0",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                str(self.output_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.0)
        if self.process.poll() is not None:
            raise RuntimeError(f"ffmpeg failed to start display capture for {self.display}")

    def stop(self) -> None:
        if not self.process:
            return
        if self.process.poll() is not None:
            return
        self.process.send_signal(signal.SIGINT)
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def capture_single_frame(display: str, width: int, height: int, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "x11grab",
            "-draw_mouse",
            "0",
            "-video_size",
            f"{width}x{height}",
            "-i",
            f"{display}.0+0,0",
            "-frames:v",
            "1",
            str(output_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@dataclass
class X11FrameCapture:
    display: str
    width: int
    height: int
    observation_shape: tuple[int, int]

    def grab_frame(self) -> np.ndarray:
        command = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "x11grab",
            "-draw_mouse",
            "0",
            "-video_size",
            f"{self.width}x{self.height}",
            "-i",
            f"{self.display}.0+0,0",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
        )
        expected_size = self.width * self.height * 3
        if len(result.stdout) != expected_size:
            raise RuntimeError(
                f"Unexpected raw frame size {len(result.stdout)} (expected {expected_size})"
            )
        return np.frombuffer(result.stdout, dtype=np.uint8).reshape((self.height, self.width, 3)).copy()

    def build_observation(self, frame: np.ndarray) -> np.ndarray:
        obs_h, obs_w = self.observation_shape
        image = Image.fromarray(frame)
        resized = image.resize((obs_w, obs_h), resample=Image.Resampling.BILINEAR)
        return np.asarray(resized, dtype=np.uint8)
