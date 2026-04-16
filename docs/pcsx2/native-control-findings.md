# Native Control Findings

## Stock PCSX2

Relevant native functions already exist in vendored PCSX2:

- `VMManager::SetPaused(bool)`
- `VMManager::FrameAdvance(u32)`
- `VMManager::SaveStateToSlot(s32, ...)`
- `VMManager::LoadStateFromSlot(s32, ...)`

The Qt UI and hotkey layer already call those functions internally.

## Stock PINE

Stock `pcsx2/PINE.cpp` exposes:

- memory read/write
- slot save/load
- game title/serial/CRC/version
- emulator status

What stock PINE did **not** expose before this change:

- native pause
- native resume
- native frame advance

What stock PINE also got wrong for deterministic external control:

- slot save/load handlers were fire-and-forget
- the IPC reply did not mean the save/load had completed
- slot save failures did not propagate as IPC failure

## Patched PINE In This Repo

The patch is intentionally narrow and lives only in `third_party/pcsx2/pcsx2/PINE.cpp`.

Added opcodes:

- `0x10`: pause
- `0x11`: resume
- `0x12`: frame advance with a little-endian `u32` frame count

Changed semantics:

- slot save uses `VMManager::SaveStateToSlot(slot, false, ...)` on the CPU thread with `block=true`
- slot load uses `VMManager::LoadStateFromSlot(slot, false, &error)` on the CPU thread with `block=true`
- `IPC_FAIL` now means:
  - no valid VM
  - invalid frame-advance request
  - RetroAchievements Hardcore mode blocks frame advance
  - slot save callback reported an error
  - slot load returned `false`

## Why This Matters For PS2Env

Before this patch:

- pause/unpause depended on synthetic hotkeys
- per-frame stepping depended on hotkeys and repeated X11 subprocess calls
- reset/init could not rely on a completed savestate load from Python

After this patch:

- pause, resume, and frame advance are native PINE operations
- a single frame-advance request can advance `N` frames
- slot load/save are deterministic enough for Python-controlled episode restore
