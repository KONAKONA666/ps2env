# Native Control Plan

## Scope Implemented

- patch PINE only
- keep slot-based savestate control
- use Xlib/XTEST for player actions
- split deployment into base and game images

## Runtime Sequence

1. Worker boots PCSX2 paused.
2. Session opens a persistent PINE connection.
3. Session opens a persistent X11 connection.
4. `init()` / `reset()` restage the baked baseline `.p2s` into the worker-local slot filename.
5. Session loads that slot synchronously over PINE.
6. `step()` sends one native `FrameAdvance(n_frames_per_step)` request.
7. Session polls PINE status until the VM is paused again.
8. The captured frame after restore is hashed and surfaced in runtime artifacts.

## Image Split

### `ps2env-base`

- starts from CUDA Ubuntu
- installs runtime graphics/X11/FFmpeg packages
- includes the NVIDIA userspace installer
- ingests a patched PCSX2 AppImage and extracts it into `/opt/pcsx2`

### `ps2env-game`

- starts from `ps2env-base`
- installs the current Python package from `/opt/ps2env`
- bakes:
  - `ps2env/`
  - `user_env/`
  - `configs/`
  - `/opt/ps2env/game/game.iso`
  - `/opt/ps2env/user/bios/`
  - `/opt/ps2env/user/sstates/baseline/episode_start.p2s`

## Build Scripts

- `scripts/build-pcsx2-appimage.sh`
- `scripts/build-base-image.sh`
- `scripts/build-game-image.sh`
- `scripts/build-image.sh`

The wrapper script rebuilds the base image only when asked or when it is missing locally, then rebuilds the game image with the baked user assets.
