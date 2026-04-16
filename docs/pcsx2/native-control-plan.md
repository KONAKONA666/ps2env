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
  - one selected env bundle, for example `/opt/ps2env/user_env/basic_ps2/`
  - `/opt/ps2env/user_env/basic_ps2/config.toml`
  - the ISO, BIOS, and baseline savestate referenced by env-root-relative config paths

## Build Scripts

- `scripts/build-pcsx2-appimage.sh`
- `scripts/build-base-image.sh`
- `build_image.py`

The game-image builder validates env-root-relative config paths, stages only the selected env bundle into the Docker build context, and builds the final image from an existing base tag.

`build_image.py` also supports:

- `--build-base` to rebuild `ps2env-base` first
- `--build-pcsx2` to force a vendored PCSX2 AppImage rebuild before rebuilding `ps2env-base`
