from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def parse_log_level(name: str) -> int:
    normalized = name.strip().upper()
    if normalized not in logging._nameToLevel:  # type: ignore[attr-defined]
        raise ValueError(f"Unsupported log level: {name}")
    return logging._nameToLevel[normalized]  # type: ignore[attr-defined]


def configure_parent_logger(level_name: str) -> logging.Logger:
    logger = logging.getLogger("ps2env.parent")
    logger.setLevel(parse_log_level(level_name))
    logger.propagate = False
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def configure_worker_logger(worker_name: str, log_path: Path, level_name: str) -> logging.Logger:
    logger = logging.getLogger(f"ps2env.{worker_name}")
    logger.setLevel(parse_log_level(level_name))
    logger.propagate = False
    if logger.handlers:
        return logger

    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(f"%(asctime)s %(levelname)s [{worker_name}] %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


@dataclass
class JsonEventLogger:
    worker_name: str
    worker_id: int
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")

    def emit(self, event: str, **fields: object) -> None:
        payload = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "worker": self.worker_name,
            "worker_id": self.worker_id,
            "event": event,
            **fields,
        }
        self._handle.write(json.dumps(payload, sort_keys=True) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()

