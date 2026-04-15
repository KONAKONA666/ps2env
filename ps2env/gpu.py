from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


GPU_HEADER_RE = re.compile(r"^GPU(\d+):$")
KEY_VALUE_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.+?)\s*$")


@dataclass(frozen=True)
class GpuAdapter:
    ordinal_index: int
    vulkan_index: int
    device_name: str
    adapter_name: str
    device_uuid: str | None


def discover_discrete_nvidia_adapters() -> list[GpuAdapter]:
    try:
        result = subprocess.run(
            ["vulkaninfo", "--summary"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("vulkaninfo is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"vulkaninfo --summary failed: {exc.stderr.strip()}") from exc

    devices: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in result.stdout.splitlines():
        header_match = GPU_HEADER_RE.match(raw_line.strip())
        if header_match:
            if current:
                devices.append(current)
            current = {"gpu_index": header_match.group(1)}
            continue

        if current is None:
            continue

        key_match = KEY_VALUE_RE.match(raw_line)
        if key_match:
            current[key_match.group(1)] = key_match.group(2)

    if current:
        devices.append(current)

    adapters: list[GpuAdapter] = []
    name_counts: dict[str, int] = {}
    for device in devices:
        if device.get("vendorID", "").lower() != "0x10de":
            continue
        if device.get("deviceType") != "PHYSICAL_DEVICE_TYPE_DISCRETE_GPU":
            continue

        base_name = device.get("deviceName")
        if not base_name:
            continue

        name_counts[base_name] = name_counts.get(base_name, 0) + 1
        suffix_index = name_counts[base_name]
        adapter_name = base_name if suffix_index == 1 else f"{base_name} ({suffix_index})"

        adapters.append(
            GpuAdapter(
                ordinal_index=len(adapters),
                vulkan_index=int(device["gpu_index"]),
                device_name=base_name,
                adapter_name=adapter_name,
                device_uuid=device.get("deviceUUID"),
            )
        )

    if not adapters:
        raise RuntimeError("No discrete NVIDIA Vulkan adapters were discovered.")

    return adapters
