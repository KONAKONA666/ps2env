# PS2Env Implementation Plan

> Historical implementation-planning document. The current implemented runtime and deployment model is described in [design.md](./design.md), [test-run.md](./test-run.md), and [pcsx2/native-control-plan.md](./pcsx2/native-control-plan.md).

## Overview

This document turns the architecture into an implementation sequence. The order is chosen to unlock deterministic stepping first, then environment authoring, then persistence/tooling, then deployment hardening.

## Phase 0: Initial Runtime Slice

### Deliverables

- Docker image with baked-in PCSX2 AppImage payload
- PS2Env runtime package
- Threaded test-run runtime
- Per-worker Xdummy + PCSX2 bring-up
- Manual artifact capture and logging

### Tasks

- Bake the provided PCSX2 AppImage into the image at build time.
- Extract the AppImage into `/opt/pcsx2`.
- Install the host-matching NVIDIA display userspace in the container at runtime before launching PCSX2.
- Launch one runtime process per container.
- Start one PS2Env worker per thread inside the runtime.
- Give each worker:
  - its own Xdummy display
  - its own PINE slot
  - an isolated output directory
- Stage a private portable PCSX2 tree per worker.
- Generate per-worker `PCSX2.ini` with:
  - Vulkan renderer
  - chosen adapter name
  - pause-on-start enabled
  - PINE enabled
  - fixed hotkeys
  - fixed keyboard Pad 1 bindings
- Capture `session.mp4` and `last_frame.png`.
- Emit `worker.log`, `events.jsonl`, and `pcsx2.log`.
- Implement `PS2Env` with:
  - `start()`
  - `init()`
  - `step()`
  - `reset()`
  - `kill()`
- Load checks, callbacks, and policies from Python files.
- Use native frame advance for deterministic waits.

### Acceptance Criteria

- The image builds from the local AppImage.
- A worker can launch PCSX2 inside Docker, render to Xdummy, record video, and step deterministically on a single visible GPU.
- The host runner can launch one shared container and execute multiple workers inside it on one visible GPU.
- `ctx.frame_count` increments exactly by `n_frames_per_step`.
- Missing BIOS, GPU, render window, or PINE socket fails clearly and leaves per-worker diagnostics.

## Phase 1: Launcher and Config Generation

### Deliverables

- TOML config loader
- Config schema validation
- Runtime directory layout
- Native PCSX2 `.ini` generator
- PCSX2 command-line generator

### Tasks

- Define the public TOML schema described in [design.md](./design.md).
- Implement translation from symbolic `ps2env` config into native PCSX2 settings.
- Write generated PCSX2 config into an env-scoped runtime directory.
- Validate and enforce hard invariants:
  - Vulkan renderer
  - start paused
  - pause on focus loss disabled
  - PINE enabled
  - portable mode enabled
  - save-state-on-shutdown disabled
  - achievements hardcore disabled
- Reject conflicting `pcsx2.extra_cli_args` and native override attempts.
- Generate the final emulator launch command:
  - `-batch`
  - `-portable`
  - boot mode flag
  - boot target

### Acceptance Criteria

- Given a TOML file, the runtime can produce a validated launcher spec.
- Generated native settings match required invariants.
- Invalid overrides fail before process launch.

## Phase 2: Display, Input, and Capture Wiring

### Deliverables

- Xdummy session bootstrap
- Render window discovery
- X11/XTest hotkey injection
- FFmpeg/X11 frame capture
- `framelayer` / `framewait` integration

### Tasks

- Start or attach to an Xdummy display for the env instance.
- Launch PCSX2 inside that display.
- Discover the PCSX2 render window, not the hidden main window.
- Generate and install a dedicated hotkey profile for:
  - pause toggle
  - frame advance
- Implement X11/XTest keyboard injection against the emulator window.
- Integrate FFmpeg/X11 frame capture for the final rendered frame.
- Wire frame counting and frame-wait helpers to support:
  - `lifecycle.frames_per_loop`
  - `stepping.n_frames_per_step`

### Acceptance Criteria

- The runtime can pause and resume the emulator reliably in Xdummy.
- The runtime can capture the render surface via FFmpeg/X11.
- Frame waits align with observed rendered-frame progression.

## Phase 3: Emulator Runtime and State Machine

### Deliverables

- Runtime state object
- Guarded public lifecycle methods
- Process teardown and failure recovery

### Tasks

- Implement stable env states:
  - `SHUTDOWN`
  - `INITIALIZATION`
  - `EPISODE`
  - `TERMINATED`
  - `TRUNCATED`
- Keep `STARTUP` as an internal-only transition within `start()`.
- Enforce method guard matrix:
  - `start` only from `SHUTDOWN`
  - `init` only from `INITIALIZATION`
  - `step` only from `EPISODE`
  - `reset` only from `EPISODE`, `TERMINATED`, or `TRUNCATED`
  - `kill` from any state
- Implement hard-failure fallback: suspend if possible, then kill and return to `SHUTDOWN`.
- Implement lifecycle timeout handling using `lifecycle.timeout_frames`.

### Acceptance Criteria

- Invalid method calls fail before touching the emulator.
- Startup, reset, and init failures leave the env in `SHUTDOWN`.
- `kill()` is idempotent and safe from any state.

## Phase 4: EnvContext and Base Actions

### Deliverables

- `EnvContext`
- Base action layer
- Context update path

### Tasks

- Implement `EnvContext` as the single runtime facade seen by env code.
- Populate context with:
  - observation
  - raw frame
  - frame count
  - step count
  - env state
  - variables
  - loaded checks
  - loaded callbacks
  - savestate helpers
  - action helpers
- Implement `ctx.update(frame=..., frame_count=...)`.
- Implement base actions for controller input and emulator helpers.
- Honor `stepping.after_action` modes:
  - `hold`
  - `press`

### Acceptance Criteria

- Policies and checks can operate entirely through `EnvContext`.
- Action handling respects configured press/hold semantics.

## Phase 5: Policies, Checks, Callbacks, and Reward

### Deliverables

- Python module loader
- Policy contract validation
- Check and callback discovery
- Reward integration

### Tasks

- Load `policy/init_policy.py`, `policy/reset_policy.py`, and `policy/step_policy.py`.
- Validate that each module exports exactly one concrete `Policy` subclass.
- Implement policy call flow:
  - `get_action(ctx, action=None)` for init/reset
  - `get_action(ctx, action)` for step
  - `take_action(ctx, resolved_action)` for all
- Load named checks from `checks_dir`.
- Load helper callables from `callbacks_dir` and attach them to `ctx`.
- Load reward logic from `env_utils`.
- Default reward to `0.0` if not provided.

### Acceptance Criteria

- Missing or ambiguous policy exports fail fast.
- Checks return `(bool, info)` and can mark termination or truncation.
- Reward is evaluated after checks and after step-count increment.

## Phase 6: Lifecycle Loop Semantics

### Deliverables

- `start()` startup loop
- `init()` loop
- `reset()` loop
- fixed `step()` flow

### Tasks

- Implement startup loop driven by `startup_check`.
- Implement init loop driven by `init_policy` and `episode_check`.
- Implement reset loop driven by `reset_policy` and `episode_check`.
- Implement fixed step flow:
  1. resolve action
  2. resume
  3. take action
  4. wait `n_frames_per_step`
  5. capture final frame
  6. pause
  7. update context
  8. increment step count
  9. run checks
  10. compute reward
  11. update env state
  12. return Gym-style output
- Keep `FrameAdvance` available only for debugging utilities.

### Acceptance Criteria

- `start()` returns only after `startup_check` passes.
- `init()` and `reset()` return only after `episode_check` passes.
- `step()` runs exactly one policy iteration and one frame-bounded window.

## Phase 7: Savestates and Variables

### Deliverables

- Named checkpoint manager
- PINE-backed savestate adapter
- Cheat-table parser
- Variable manager

### Tasks

- Implement named checkpoint mapping:
  - `startup -> 1`
  - `reset -> 2`
  - `scratch -> 10`
- Wrap PINE save/load slot operations behind checkpoint helpers.
- Keep raw slot access as an advanced path only.
- Parse Cheat Engine `.CT` files into logical variables.
- Read variables through PINE memory access.
- Majority-vote duplicate address reads.
- Write values to every backing address for the variable group.
- Cache the last stable value for tie fallback on reads.

### Acceptance Criteria

- Checkpoints save and load correctly through slot mapping.
- Duplicate-address variables resolve consistently.
- Variable writes propagate to every backing address.

## Phase 8: Tools

### Deliverables

- Cheat companion tool
- Record tool
- Analyze tool
- Train tool

### Tasks

- Implement a cheat-table inspection tool using the shared config/runtime model.
- Implement game recording with synchronized frame and input output.
- Implement frame-by-frame analysis with classifier labeling into:
  - `root/{cls_name}/{i}.png`
- Implement classifier training flow for the ONNX-compatible MobileNet small model.

### Acceptance Criteria

- Tooling reuses the core runtime conventions where practical.
- Recorded sessions can be analyzed and converted into classifier training data.

## Phase 9: Containerization and Rollout Outline

### Deliverables

- Container dependency spec
- Runtime health checks
- Rollout server boundary document

### Tasks

- Build the PS2Env image from the provided NVIDIA/X11/Vulkan template.
- Remove Proton/Wine-specific layers and install PCSX2-targeted dependencies.
- Ensure Vulkan works inside containerized Xdummy sessions.
- Define rollout server responsibilities:
  - execute env lifecycle RPCs
  - host GPU container
  - expose health and logs
- Define training server responsibilities:
  - own RL orchestration
  - call env lifecycle methods
  - not mirror game state

### Acceptance Criteria

- One container can run one env instance with GPU rendering, Xdummy, FFmpeg/X11 capture, and PINE.
- The rollout server remains executor-only.

## Cross-Cutting Validation Matrix

The following validations apply across phases:

- State guards from `SHUTDOWN`, `INITIALIZATION`, `EPISODE`, `TERMINATED`, and `TRUNCATED`
- `start()` reaches `startup_check`
- `init()` and `reset()` reach `episode_check`
- `step()` advances exactly `n_frames_per_step` rendered frames
- Pause/resume hotkey control remains stable in Xdummy
- FFmpeg/X11 capture works in `-batch -nogui`
- PINE connectivity works for status, memory, and savestate slot control
- Generated PCSX2 settings preserve Vulkan, pause, and IPC invariants

For the implemented runtime, manual inspection of `session.mp4`, `last_frame.png`, and `events.jsonl` remains an important debugging path.

Observed constraint after real lifecycle runs:

- PCSX2 + Vulkan + Xdummy is stable when each worker container sees exactly one GPU.
- A container with multiple visible GPUs does not provide a presentable surface for every GPU, so machine-level multi-worker scheduling must be implemented with one container per worker.

Implemented env result:

- `PS2Env` runs successfully with native frame-advance stepping.
- The shared-container env runner works with multiple workers on the first visible GPU.
