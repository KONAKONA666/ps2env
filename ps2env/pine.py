from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass
from pathlib import Path

PINE_DEFAULT_SLOT = 28011
PINE_OK = 0
PINE_FAIL = 0xFF
PINE_MSG_STATUS = 0x0F


class PINEStatus:
    RUNNING = "running"
    PAUSED = "paused"
    SHUTDOWN = "shutdown"


@dataclass
class PineClient:
    socket_path: Path
    timeout_seconds: float = 1.0

    def _exchange(self, payload: bytes) -> bytes:
        message = struct.pack("<I", len(payload) + 4) + payload
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(self.timeout_seconds)
            client.connect(str(self.socket_path))
            client.sendall(message)

            header = self._recv_exact(client, 4)
            total_size = struct.unpack("<I", header)[0]
            body = self._recv_exact(client, total_size - 4)
        if not body:
            raise RuntimeError("PINE returned an empty response.")
        if body[0] == PINE_FAIL:
            raise RuntimeError("PINE returned a failure response.")
        return body[1:]

    def _recv_exact(self, client: socket.socket, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = client.recv(remaining)
            if not chunk:
                raise RuntimeError("PINE connection closed unexpectedly.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def get_status(self) -> str:
        payload = self._exchange(bytes([PINE_MSG_STATUS]))
        if len(payload) != 4:
            raise RuntimeError(f"Unexpected PINE status payload size: {len(payload)}")
        status_value = struct.unpack("<I", payload)[0]
        if status_value == 0:
            return PINEStatus.RUNNING
        if status_value == 1:
            return PINEStatus.PAUSED
        return PINEStatus.SHUTDOWN


def pine_socket_path(xdg_runtime_dir: str | Path, slot: int) -> Path:
    base = Path(xdg_runtime_dir) / "pcsx2.sock"
    if slot == PINE_DEFAULT_SLOT:
        return base
    return Path(f"{base}.{slot}")


def wait_for_pine_socket(path: Path, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if path.exists():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(1.0)
                    client.connect(str(path))
                return
            except OSError as exc:
                last_error = exc
        time.sleep(0.25)

    detail = f" Last error: {last_error}" if last_error else ""
    raise TimeoutError(f"Timed out waiting for PINE socket at {path}.{detail}")
