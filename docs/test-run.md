# Test Runner

## Overview

`test_run.py` is the host-side launcher for lifecycle stress testing.

It runs one Docker container on one visible GPU, starts `ps2env.test_run_runtime` inside that container, and drives `PS2Env` workers through:

- `start()`
- `init()`
- `step()`
- `reset()`
- `kill()`

The initial bootstrap is gated by `--n-parallel-starts`. After bootstrap, restart-driven `start()` calls only respect the shared start semaphore.

## Image Contract

The runner assumes the chosen image already contains the full runtime payload.

For the smoke image, that means:

- `/opt/ps2env/ps2env`
- `/opt/ps2env/user_env/basic_ps2/config.toml`
- `/opt/ps2env/user_env/basic_ps2/Shadow of the Colossus [RUS NTSC].ISO`
- `/opt/ps2env/user_env/basic_ps2/assets/bios/`
- `/opt/ps2env/user_env/basic_ps2/states/episode_start.p2s`

`test_run.py` no longer bind-mounts the repo, ISO, BIOS directory, or savestate file into the container.

It mounts only:

- `outdir -> /workspace/output`
- `cache-dir -> /workspace/cache`

## Build Flow

Build the vendored PCSX2 AppImage when needed:

```bash
scripts/build-pcsx2-appimage.sh
```

Build the base image:

```bash
scripts/build-base-image.sh --tag ps2env-base:dev
```

Build the game image from the env-root config:

```bash
python3 build_image.py \
  --config user_env/basic_ps2/config.toml \
  --base-image-tag ps2env-base:dev \
  --game-image-tag ps2env-smoke:dev
```

The selected env bundle must already contain or symlink the ISO, BIOS directory, and baseline savestate using env-root-relative paths from `config.toml`.

Optional build flags:

- `--build-base`: rebuild `ps2env-base` before the game image build
- `--build-pcsx2`: force a vendored PCSX2 AppImage rebuild before rebuilding `ps2env-base`; implies `--build-base`

## Smoke Command

Run exactly:

```bash
python3 test_run.py \
  --config user_env/basic_ps2/config.toml \
  --actions '[0]' \
  --num-steps 1000 \
  --reset-steps 200 \
  --restart-steps 500 \
  --n-parallel-starts 1 \
  --num-workers 1 \
  --outdir /tmp/ps2env-test-run-real \
  --image ps2env-smoke:dev
```

Expected lifecycle counts for this config:

- `starts = 2`
- `inits = 2`
- `restarts = 1`
- `resets = 4`

`user_env/basic_ps2/config.toml` disables the old `step_limit` truncation so scheduled resets and restarts dominate the run.

## Output Artifacts

The final output layout is flat under the requested `outdir`:

- `runner.log`
- `container-env.log`
- `summary.json`
- `worker-00/`

Each worker directory contains:

- `worker.log`
- `events.jsonl`
- `timings.jsonl`
- `session.mp4`
- `pcsx2.log`
- `pcsx2-console.log`
- `xorg.log`
- `last_frame.png`
- debug screenshots such as `start_error.png`, `init_error.png`, `reset_error.png`, or `step_error.png`

`events.jsonl` and `timings.jsonl` include result summaries for `start`, `init`, `reset`, and `step`, including frame hashes for `init()` and `reset()` results.
