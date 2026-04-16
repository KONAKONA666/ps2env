# Design

## Architecture

`ps2env` owns lifecycle state, policies, checks, and the host-side smoke harness.

Vendored [third_party/pcsx2](../third_party/pcsx2) is the emulator backend. The only emulator patch carried here for control is in `pcsx2/PINE.cpp`.

The deployment model is split into two images:

1. `ps2env-base`
   - patched PCSX2
   - X11/Xdummy/FFmpeg/Vulkan runtime
   - host-matching NVIDIA userspace installer
2. `ps2env-game`
   - current `ps2env` code
   - `user_env`
   - image-local configs
   - baked ISO, BIOS, and baseline savestate

## Control Surfaces

| Capability | Surface | Notes |
| --- | --- | --- |
| Launch/config | generated `.ini` + CLI | worker-local portable PCSX2 tree |
| Pause | patched PINE | native `VMManager::SetPaused(true)` |
| Resume | patched PINE | native `VMManager::SetPaused(false)` |
| Frame advance | patched PINE | native `VMManager::FrameAdvance(u32)` |
| Status | PINE | `Running`, `Paused`, `Shutdown` |
| Savestate load/save | PINE slot ops | synchronous slot load/save semantics |
| Memory read/write | PINE | unchanged stock PINE path |
| Player actions | Xlib/XTEST | persistent X connection, no `xdotool` |
| Video/debug screenshots | FFmpeg/X11 | whole-display capture |

The runtime no longer depends on PCSX2 hotkeys for pause or frame advance.

## Worker Lifecycle

Each worker:

- stages a private portable PCSX2 tree from `/opt/pcsx2`
- writes a worker-local `PCSX2.ini`
- starts its own Xdummy server and PCSX2 process
- owns one PINE slot and one X11 display

After boot:

- `PCSX2Session.ensure_paused()` uses native PINE pause
- `advance_frames(n)` sends one native `FrameAdvance(n)` request and polls PINE until the VM is paused again
- `ctx.frame_count` remains env-owned and increments by the requested frame count

## Savestate Model

The implemented savestate model is slot-based.

Config fields:

```toml
[savestates]
episode_start_file = "/opt/ps2env/user/sstates/baseline/episode_start.p2s"
episode_start_slot = 1
```

Behavior:

- `start()` boots PCSX2 and leaves the VM paused.
- Before every `init()` and `reset()`, the session:
  - queries serial and CRC over PINE
  - computes the canonical slot filename (`SERIAL (CRC).NN.p2s`)
  - restages the baked baseline state into the worker-local `sstates/` directory
  - loads that slot synchronously over PINE
- `init()` and `reset()` return a `frame_hash` derived from the captured frame after restore.

This gives deterministic episode entry without adding file-path save/load IPC.

## Config And Paths

Tracked configs now use image-local paths such as:

- `/opt/ps2env/game/game.iso`
- `/opt/ps2env/user/bios`
- `/opt/ps2env/user/sstates/baseline/episode_start.p2s`

Relative Python module paths inside the TOML continue to resolve correctly because configs live under `/opt/ps2env/configs/`.

Legacy `input.pause_hotkey` and `input.frame_advance_hotkey` are still accepted by the parser for compatibility, but they are ignored by the runtime.

## Testing Expectations

The canonical smoke run is documented in [test-run.md](./test-run.md).

The acceptance signal is:

- successful 1000-step run
- scheduled resets/restart at the expected counts
- no runtime `xdotool` usage
- matching `frame_hash` values immediately after initial `init()` and subsequent scheduled `reset()` / post-restart `init()`
