from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

PINE_DEFAULT_SLOT = 28011
PINE_OK = 0
PINE_FAIL = 0xFF

PINE_MSG_SAVE_STATE = 0x09
PINE_MSG_LOAD_STATE = 0x0A
PINE_MSG_ID = 0x0C
PINE_MSG_UUID = 0x0D
PINE_MSG_STATUS = 0x0F
PINE_MSG_PAUSE = 0x10
PINE_MSG_RESUME = 0x11
PINE_MSG_FRAME_ADVANCE = 0x12


class PINEStatus:
    RUNNING = "running"
    PAUSED = "paused"
    SHUTDOWN = "shutdown"


@dataclass
class PineClient:
    socket_path: Path
    timeout_seconds: float = 1.0
    _client: socket.socket | None = field(default=None, init=False, repr=False)

    def connect(self) -> None:
        if self._client is not None:
            return
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(self.timeout_seconds)
        try:
            client.connect(str(self.socket_path))
        except Exception:
            client.close()
            raise
        self._client = client

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.close()
        finally:
            self._client = None

    def _ensure_connected(self) -> socket.socket:
        self.connect()
        assert self._client is not None
        return self._client

    def _exchange(self, payload: bytes) -> bytes:
        message = struct.pack("<I", len(payload) + 4) + payload
        last_error: Exception | None = None
        for attempt in range(2):
            client = self._ensure_connected()
            try:
                client.sendall(message)
                header = self._recv_exact(client, 4)
                total_size = struct.unpack("<I", header)[0]
                body = self._recv_exact(client, total_size - 4)
                if not body:
                    raise ConnectionError("PINE returned an empty response.")
                if body[0] == PINE_FAIL:
                    raise RuntimeError("PINE returned a failure response.")
                return body[1:]
            except OSError as exc:
                last_error = exc
                self.close()
                if attempt == 0:
                    continue
                raise
        assert last_error is not None
        raise last_error

    def _recv_exact(self, client: socket.socket, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = client.recv(remaining)
            if not chunk:
                raise ConnectionError("PINE connection closed unexpectedly.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _get_string(self, opcode: int) -> str:
        payload = self._exchange(bytes([opcode]))
        if len(payload) < 4:
            raise RuntimeError(f"Unexpected PINE string payload size: {len(payload)}")
        size = struct.unpack("<I", payload[:4])[0]
        raw = payload[4:]
        if len(raw) != size:
            raise RuntimeError(f"Unexpected PINE string payload body size: {len(raw)} (expected {size})")
        return raw.rstrip(b"\x00").decode("utf-8")

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

    def pause(self) -> None:
        self._exchange(bytes([PINE_MSG_PAUSE]))

    def resume(self) -> None:
        self._exchange(bytes([PINE_MSG_RESUME]))

    def frame_advance(self, frame_count: int) -> None:
        if frame_count < 1:
            raise ValueError("frame_count must be >= 1")
        self._exchange(bytes([PINE_MSG_FRAME_ADVANCE]) + struct.pack("<I", int(frame_count)))

    def save_state_slot(self, slot: int) -> None:
        if slot < 0 or slot > 255:
            raise ValueError("slot must be between 0 and 255")
        self._exchange(bytes([PINE_MSG_SAVE_STATE, int(slot)]))

    def load_state_slot(self, slot: int) -> None:
        if slot < 0 or slot > 255:
            raise ValueError("slot must be between 0 and 255")
        self._exchange(bytes([PINE_MSG_LOAD_STATE, int(slot)]))

    def get_game_id(self) -> str:
        return self._get_string(PINE_MSG_ID)

    def get_game_crc(self) -> int:
        raw_crc = self._get_string(PINE_MSG_UUID).strip()
        if not raw_crc:
            raise RuntimeError("PINE returned an empty game CRC.")
        return int(raw_crc, 16)


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
