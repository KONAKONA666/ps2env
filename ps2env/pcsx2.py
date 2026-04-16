from __future__ import annotations

import os
import shutil
import subprocess
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .config import PS2EnvConfig
from .controller_mapping import PAD1_KEYBOARD_BINDINGS


SETTINGS_VERSION = 1
VULKAN_RENDERER = "14"


@dataclass(frozen=True)
class WorkerPcsx2Layout:
    root: Path
    app_root: Path
    binary_path: Path
    settings_path: Path


def stage_worker_pcsx2_tree(source_root: Path, destination_root: Path) -> WorkerPcsx2Layout:
    if destination_root.exists():
        shutil.rmtree(destination_root)
    destination_root.parent.mkdir(parents=True, exist_ok=True)
    destination_root.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            ["cp", "-al", f"{source_root}/.", str(destination_root)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        shutil.rmtree(destination_root, ignore_errors=True)
        shutil.copytree(source_root, destination_root, symlinks=True)

    app_root = destination_root / "usr" / "bin"
    settings_path = app_root / "inis" / "PCSX2.ini"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    return WorkerPcsx2Layout(
        root=destination_root,
        app_root=app_root,
        binary_path=app_root / "pcsx2-qt",
        settings_path=settings_path,
    )


def select_bios_file(bios_dir: Path, bios_file: str | None) -> Path:
    if not bios_dir.is_dir():
        raise FileNotFoundError(f"BIOS directory does not exist: {bios_dir}")

    if bios_file:
        explicit = Path(bios_file)
        candidate = explicit if explicit.is_absolute() else bios_dir / explicit
        if not candidate.is_file():
            raise FileNotFoundError(f"Configured BIOS file does not exist: {candidate}")
        return candidate

    preferred_suffixes = [".bin", ".rom0", ".rom1", ".diff"]
    candidates_by_suffix: dict[str, list[Path]] = {suffix: [] for suffix in preferred_suffixes}
    for path in sorted(bios_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.stat().st_size < 1024 * 1024:
            continue
        suffix = path.suffix.lower()
        if suffix in candidates_by_suffix:
            candidates_by_suffix[suffix].append(path)

    for suffix in preferred_suffixes:
        if candidates_by_suffix[suffix]:
            return candidates_by_suffix[suffix][0]

    if not any(candidates_by_suffix.values()):
        raise FileNotFoundError(f"No BIOS image candidates were found in {bios_dir}")
    raise FileNotFoundError(f"Unsupported BIOS file layout in {bios_dir}")


def _write_ini(path: Path, sections: OrderedDict[str, list[tuple[str, str]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for section, entries in sections.items():
            handle.write(f"[{section}]\n")
            for key, value in entries:
                handle.write(f"{key} = {value}\n")
            handle.write("\n")


def write_worker_settings(
    layout: WorkerPcsx2Layout,
    config: PS2EnvConfig,
    *,
    bios_file: Path,
    adapter_name: str,
    pine_slot: int,
    pcsx2_log_path: Path,
) -> None:
    bios_dir = bios_file.parent
    sections: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
    sections["UI"] = [
        ("SettingsVersion", str(SETTINGS_VERSION)),
        ("SetupWizardIncomplete", "false"),
        ("StartPaused", "true"),
        ("PauseOnFocusLoss", "false"),
        ("PauseOnMenu", "false"),
        ("ConfirmShutdown", "false"),
        ("RenderToSeparateWindow", "false"),
        ("HideMainWindowWhenRunning", "false"),
        ("HideMouseCursor", "true"),
        ("StartFullscreen", "false"),
    ]
    sections["Logging"] = [
        ("EnableSystemConsole", "false"),
        ("EnableFileLogging", "true"),
        ("EnableTimestamps", "true"),
        ("EnableControllerLogs", "false"),
    ]
    sections["Folders"] = [
        ("Bios", str(bios_dir)),
        ("Logs", "logs"),
        ("Savestates", "sstates"),
        ("MemoryCards", "memcards"),
        ("Snapshots", "snaps"),
        ("Cheats", "cheats"),
        ("Patches", "patches"),
        ("UserResources", "resources"),
        ("Cache", "cache"),
        ("Textures", "textures"),
        ("InputProfiles", "inputprofiles"),
        ("Videos", "videos"),
    ]
    sections["Filenames"] = [
        ("BIOS", bios_file.name),
    ]
    sections["InputSources"] = [
        ("Keyboard", "true"),
        ("Pointer", "false"),
        ("SDL", "false"),
    ]
    sections["Pad"] = [
        ("MultitapPort1", "false"),
        ("MultitapPort2", "false"),
    ]
    sections["EmuCore"] = [
        ("EnableFastBoot", "true" if config.game.fastboot else "false"),
        ("EnablePINE", "true"),
        ("PINESlot", str(pine_slot)),
        ("SaveStateOnShutdown", "false"),
        ("BackupSavestate", "false"),
        ("SavestateCompressionType", "0"),
        ("SavestateCompressionRatio", "0"),
    ]
    sections["EmuCore/GS"] = [
        ("Renderer", VULKAN_RENDERER),
        ("Adapter", adapter_name),
    ]
    sections["SPU2/Output"] = [
        ("Backend", "Null"),
    ]
    sections["Pad1"] = [("Type", "DualShock2"), *PAD1_KEYBOARD_BINDINGS.items()]
    _write_ini(layout.settings_path, sections)

    # The runtime writes its own logs outside the portable tree, but we still
    # pre-create the PCSX2 log directory so -logfile can succeed deterministically.
    (layout.app_root / "logs").mkdir(parents=True, exist_ok=True)
    pcsx2_log_path.parent.mkdir(parents=True, exist_ok=True)


def build_worker_environment(
    layout: WorkerPcsx2Layout,
    *,
    display: str,
    xdg_runtime_dir: Path,
    home_dir: Path,
) -> dict[str, str]:
    env = os.environ.copy()
    env["DISPLAY"] = display
    env["HOME"] = str(home_dir)
    env["XDG_RUNTIME_DIR"] = str(xdg_runtime_dir)
    env["QT_QPA_PLATFORM"] = "xcb"
    env["SDL_VIDEODRIVER"] = "x11"
    env["QT_PLUGIN_PATH"] = str(layout.root / "usr" / "plugins")
    for nvidia_icd in ("/etc/vulkan/icd.d/nvidia_icd.json", "/usr/share/vulkan/icd.d/nvidia_icd.json"):
        if Path(nvidia_icd).exists():
            env["VK_ICD_FILENAMES"] = nvidia_icd
            break
    ld_library_path = str(layout.root / "usr" / "lib")
    existing_ld = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = (
        f"{ld_library_path}:{existing_ld}" if existing_ld else ld_library_path
    )
    return env


def build_launch_command(
    layout: WorkerPcsx2Layout,
    *,
    iso_path: str,
    pcsx2_log_path: Path,
    fastboot: bool,
) -> list[str]:
    command = [
        str(layout.binary_path),
        "-portable",
        "-batch",
        "-logfile",
        str(pcsx2_log_path),
    ]
    command.append("-fastboot" if fastboot else "-slowboot")
    command.append(iso_path)
    return command
