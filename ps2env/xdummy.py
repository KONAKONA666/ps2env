from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


MODELINES: dict[tuple[int, int], str] = {
    (640, 360): '28.56 640 664 728 816 360 363 368 389 -hsync +vsync',
    (1280, 720): '74.50 1280 1344 1472 1664 720 723 728 748 -hsync +vsync',
    (1920, 1080): '173.00 1920 2048 2248 2576 1080 1083 1088 1120 -hsync +vsync',
}


def write_xorg_dummy_config(path: Path, width: int, height: int) -> None:
    modeline = MODELINES.get((width, height))
    if modeline is None:
        supported = ", ".join(f"{w}x{h}" for w, h in sorted(MODELINES))
        raise ValueError(f"Unsupported Xdummy resolution {width}x{height}. Supported modes: {supported}")

    vram_kb = max(256000, ((width * height * 4 * 2) // 1024) + 1024)
    config = f"""
Section "ServerFlags"
    Option "DontVTSwitch" "on"
    Option "AllowMouseOpenFail" "on"
    Option "PciForceNone" "on"
    Option "AllowEmptyInput" "on"
    Option "AutoEnableDevices" "off"
    Option "AutoAddDevices" "off"
EndSection

Section "ServerLayout"
    Identifier "Layout0"
    Screen 0 "Screen0"
EndSection

Section "Device"
    Identifier "DummyDevice"
    Driver "dummy"
    DacSpeed 30000
    Option "ConstantDPI" "true"
    VideoRam {vram_kb}
EndSection

Section "Monitor"
    Identifier "Monitor0"
    HorizSync 1.0-300000.0
    VertRefresh 1.0-300.0
    Modeline "{width}x{height}" {modeline}
    Option "PreferredMode" "{width}x{height}"
EndSection

Section "Screen"
    Identifier "Screen0"
    Device "DummyDevice"
    Monitor "Monitor0"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Modes "{width}x{height}"
    EndSubSection
EndSection
""".strip()
    path.write_text(config + "\n", encoding="ascii")


@dataclass
class XDummyServer:
    display_number: int
    width: int
    height: int
    config_path: Path
    log_path: Path
    process: subprocess.Popen[bytes] | None = None

    @property
    def display(self) -> str:
        return f":{self.display_number}"

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        write_xorg_dummy_config(self.config_path, self.width, self.height)
        self.process = subprocess.Popen(
            [
                "Xorg",
                self.display,
                "-noreset",
                "+extension",
                "GLX",
                "+extension",
                "RANDR",
                "+extension",
                "RENDER",
                "-ac",
                "-nolisten",
                "tcp",
                "-config",
                str(self.config_path),
                "-logfile",
                str(self.log_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def wait_until_ready(self, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                raise RuntimeError(f"Xorg exited early for display {self.display}. See {self.log_path}")
            result = subprocess.run(
                ["xdpyinfo", "-display", self.display],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                return
            time.sleep(0.25)
        raise TimeoutError(f"Timed out waiting for Xdummy display {self.display}")

    def stop(self) -> None:
        if not self.process:
            return
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
