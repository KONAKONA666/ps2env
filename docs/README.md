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
  - one selected env bundle rooted at `user_env/<env-name>/`
  - that env bundle's `config.toml`
  - the game ISO, BIOS files, and baseline savestate referenced by env-root-relative paths
- Each worker stages a private portable PCSX2 tree from `/opt/pcsx2`, generates a worker-local `PCSX2.ini`, and runs against its own Xdummy display and PINE slot.

## Current Control Plane

- Launch/config: generated PCSX2 `.ini` plus CLI flags
- Native pause/resume/frame advance: patched PINE IPC
- Memory/status/savestates: PINE IPC
- Gamepad/keyboard actions: in-process Xlib/XTEST injection
- Recording/screenshots: FFmpeg/X11 capture

## Active Config Surface

Canonical smoke configs live in env roots such as [user_env/basic_ps2/config.toml](../user_env/basic_ps2/config.toml).

Important keys:

- `game.iso_path`
- `game.bios_dir`
- `game.bios_file`
- `game.actions`
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

All path-bearing config values are relative to the env root that contains `config.toml`.

If `game.actions` is configured, `step()` takes `[action_idx, *action_args]` payloads and resolves `action_idx` through modules in the fixed env-root `actions/` directory.

`input.pause_hotkey` and `input.frame_advance_hotkey` remain parser-compatible for older configs, but the runtime no longer uses or generates those hotkeys.

## Default Pad1 Keymap

The generated DualShock 2 keyboard bindings are:

- `D-Pad Up/Right/Down/Left -> Up/Right/Down/Left`
- `Triangle/Circle/Cross/Square -> i/l/k/j`
- `Select/Start -> Backspace/Return`
- `L1/L2/L3 -> q/1/2`
- `R1/R2/R3 -> e/3/4`
- `Analog Toggle/Apply Pressure -> minus`
- `Left Stick Up/Right/Down/Left -> w/d/s/a`
- `Right Stick Up/Right/Down/Left -> t/h/g/f`

The sample Shadow of the Colossus env uses:

- `jump`: `[0, hold_r1]`
- `move`: `[1, hold_r1, dir0, ...]`
- `combat`: `[2]`

For `move`, directions are `0=forward`, `1=backward`, `2=left`, `3=right`.

## Current Smoke Target

The tracked smoke config is [user_env/basic_ps2/config.toml](../user_env/basic_ps2/config.toml).

It assumes the `ps2env-game` image already contains:

- `/opt/ps2env/user_env/basic_ps2/config.toml`
- `/opt/ps2env/user_env/basic_ps2/Shadow of the Colossus [RUS NTSC].ISO`
- `/opt/ps2env/user_env/basic_ps2/assets/bios/`
- `/opt/ps2env/user_env/basic_ps2/states/episode_start.p2s`

The host-side smoke runner now mounts only:

- the output directory
- the cache directory used by the NVIDIA installer

Use `build_image.py --build-base` to rebuild `ps2env-base` as part of the game-image flow, or `--build-pcsx2` to force a vendored PCSX2 AppImage rebuild before that base-image rebuild.
