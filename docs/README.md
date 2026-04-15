# PS2Env Documentation

This folder is the working source of truth for the PS2Env architecture.

PS2Env is a Gym-style reinforcement learning environment runner for PlayStation 2 games, built around PCSX2 and designed to run reproducibly inside GPU-enabled Docker containers. The key design choice in v1 is that `ps2env` owns runtime state and configuration, while vendored PCSX2 is treated as an emulator backend that is configured and driven by the framework.

## Reading Order

1. [design.md](./design.md)  
   Runtime model, state machine, control split, config schema, policy contract, savestates, variables, and deployment assumptions.
2. [plan.md](./plan.md)  
   Architecture decisions, subsystem boundaries, and phase-level delivery plan.
3. [implementation-plan.md](./implementation-plan.md)  
   Concrete work breakdown, acceptance criteria, and validation targets.

## Current Decisions

- `ps2env` TOML is the authoritative user-facing config format.
- Native PCSX2 `.ini` files and launch flags are generated from that TOML.
- Vendored [third_party/pcsx2](../third_party/pcsx2) is the emulator reference for v1.
- Vulkan is the required production renderer in Linux/Docker deployments.
- The first executable slice is a smoke runtime, not the final Gym environment lifecycle.
- PCSX2 is controlled through a split control plane:
  - Launch/config: generated CLI and `.ini`
  - Pause/resume/frame advance: X11/XTest-triggered PCSX2 hotkeys
  - Memory, savestates, and status: PINE IPC
  - Final observation capture target: XShm against the emulator render window
  - Implemented smoke-slice capture: FFmpeg/X11 full-display recording plus a final PNG frame
- `ps2env` owns all environment state. There is no separate external game-state tracker.
- The solved smoke path uses one GPU-visible container per worker.
- GPU assignment is round-robin by worker index at the host runner level.

## v1 Scope

- Core environment lifecycle: `start`, `init`, `step`, `reset`, `kill`
- Policy-driven startup/init/reset/step loops
- Frame-based stepping with deterministic pause boundaries
- Savestate-backed checkpoints
- Cheat-table-backed game variables
- Quality-of-life tools for recording, analysis, and classifier training
- Container and rollout architecture outline for remote execution

## Out of Scope for v1

- A custom PCSX2 fork or custom emulator IPC
- Distributed scheduler design beyond a thin rollout server outline
- Automatic in-game state modeling outside env-defined checks, callbacks, variables, and observation processing
- A second configuration source of truth besides `ps2env` TOML

## Reference Inputs

The design in this folder is based on:

- The subsystem and lifecycle requirements described for PS2Env
- The vendored PCSX2 source tree in [third_party/pcsx2](../third_party/pcsx2)
- The provided NVIDIA/Vulkan/X11 Docker template as the deployment baseline

## Smoke Bring-Up Workflow

Image build:

```bash
scripts/build-image.sh \
  --pcsx2-appimage ~/pcsx2-v2.6.3-linux-appimage-x64-Qt.AppImage \
  --tag ps2env-smoke:latest
```

Smoke run:

```bash
scripts/run-game.sh \
  --game "/home/konakona666/Downloads/Shadow of the Colossus [RUS NTSC].ISO" \
  --bios-dir /path/to/ps2-bios \
  --workers 2 \
  --duration-seconds 30 \
  --output-dir ./output \
  --image ps2env-smoke:latest
```

`run-game.sh` also accepts `PS2ENV_BIOS_DIR` as the default BIOS directory if `--bios-dir` is omitted.
`PS2ENV_GPU_LIST` may be set to a comma-separated list such as `0` or `0,2` to restrict worker scheduling to a known-good subset of host GPUs.

The smoke runtime will scan the BIOS directory recursively and pick the first plausible BIOS image, preferring `.BIN` files.
For Docker bring-up, the container also installs the host-matching NVIDIA display userspace at runtime before launching PCSX2.
`run-game.sh` launches one container per worker and binds exactly one GPU into each container with `--gpus device=<index>`.

## Artifact Layout

Each run is stored under:

`<output-dir>/<run-id>/worker-XX/`

Per worker, the runtime writes:

- `worker.log`
- `events.jsonl`
- `pcsx2.log`
- `pcsx2-console.log`
- `xorg.log`
- `smoke.mp4`
- `last_frame.png`

The smoke slice is intentionally validated by inspecting these artifacts manually.
Each worker container also writes:

- `<output-dir>/<run-id>/container-worker-XX.log`

## Environment Workflow

Shared-container environment run on the first known-good GPU:

```bash
export PS2ENV_GPU_LIST=0

scripts/run-env.sh \
  --game "/home/konakona666/Downloads/Shadow of the Colossus [RUS NTSC].ISO" \
  --bios-dir "/home/konakona666/ps2-bios-usa/ps2 bios usa" \
  --workers 2 \
  --steps 4 \
  --output-dir ./output \
  --image ps2env-smoke:dev
```

This launches one container with one visible GPU and runs multiple env workers inside it.

Environment config now includes:

- `capture.game_fps`
- `capture.observation_shape`
- `stepping.n_frames_per_step`
- `lifecycle.frames_per_loop`
- `lifecycle.timeout_frames`
- `game.startup_check`
- `game.episode_check`
- `game.step_checks`
- `game.checks_dir`
- `game.callbacks_dir`
- `game.policy_dir`
- `game.env_utils`

## Current Bring-Up Status

What is working:

- Docker image build from the local PCSX2 AppImage
- BIOS discovery from recursive BIOS directories
- Per-worker Xdummy startup
- Per-worker PCSX2 launch
- BIOS + ISO load
- PINE socket bring-up
- Render window creation
- FFmpeg capture
- Unpause hotkey delivery
- Random action injection
- Host-level round-robin GPU assignment through one worker container per visible GPU
- Real `PS2Env` lifecycle inside Docker:
  - `start()`
  - `init()`
  - `step()`
  - `kill()`
- Deterministic env-owned `frame_count` increments by `n_frames_per_step`
- Python checks, callbacks, and policies load from `.py` files
- Shared-container env runtime works with multiple workers on the first visible GPU
- `reset()` was validated by truncating on `step_limit` and returning to `EPISODE`
- The smoke fallback path still works on the rebuilt image

Known deployment constraint:

- A container with multiple visible GPUs is not usable for PCSX2 + Vulkan + Xdummy round-robin presentation.
- The stable solution is one visible GPU per worker container, with multiple worker containers per machine.
