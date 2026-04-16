# PS2Env Documentation

`ps2env` is a PCSX2-backed PS2 environment runner for deterministic lifecycle testing inside GPU-enabled Docker containers.

## Read First

- [design.md](./design.md): current runtime design and control surfaces
- [pcsx2/native-control-findings.md](./pcsx2/native-control-findings.md): stock PCSX2 vs patched PINE behavior
- [pcsx2/native-control-plan.md](./pcsx2/native-control-plan.md): implementation notes for the native control path
- [test-run.md](./test-run.md): build flow, smoke command, lifecycle semantics, and artifacts

Fresh clones must initialize the forked PCSX2 submodule:

```bash
git submodule update --init --recursive
```

## Current Runtime Model

- `ps2env-base` contains patched PCSX2, graphics/runtime dependencies, and the host-matching NVIDIA userspace installer.
- `ps2env-game` is built from `ps2env-base` and bakes:
  - current `ps2env` Python code
  - `user_env`
  - image-local configs
  - the game ISO
  - BIOS files
  - the baseline savestate used for deterministic `init()` and `reset()`
- Each worker stages a private portable PCSX2 tree from `/opt/pcsx2`, generates a worker-local `PCSX2.ini`, and runs against its own Xdummy display and PINE slot.

## Current Control Plane

- Launch/config: generated PCSX2 `.ini` plus CLI flags
- Native pause/resume/frame advance: patched PINE IPC
- Memory/status/savestates: PINE IPC
- Gamepad/keyboard actions: in-process Xlib/XTEST injection
- Recording/screenshots: FFmpeg/X11 capture

## Active Config Surface

Canonical configs live in [configs/](../configs/).

Important keys:

- `game.iso_path`
- `game.bios_dir`
- `game.bios_file`
- `game.startup_check`
- `game.episode_check`
- `game.step_checks`
- `workers.display_base`
- `workers.pine_slot_base`
- `capture.width`
- `capture.height`
- `capture.game_fps`
- `stepping.n_frames_per_step`
- `lifecycle.frames_per_loop`
- `lifecycle.timeout_frames`
- `savestates.episode_start_file`
- `savestates.episode_start_slot`

`input.pause_hotkey` and `input.frame_advance_hotkey` remain parser-compatible for older configs, but the runtime no longer uses or generates those hotkeys.

## Current Smoke Target

The tracked smoke config is [configs/config.toml](../configs/config.toml).

It assumes the `ps2env-game` image already contains:

- `/opt/ps2env/game/game.iso`
- `/opt/ps2env/user/bios/`
- `/opt/ps2env/user/sstates/baseline/episode_start.p2s`

The host-side smoke runner now mounts only:

- the output directory
- the cache directory used by the NVIDIA installer
