# PS2Env Architecture Design

## Purpose

PS2Env provides a Gym-style RL environment API for PlayStation 2 games running in PCSX2. The framework is optimized for deterministic stepping, remote execution in GPU-enabled Docker containers, and user-defined environment logic through policies, checks, callbacks, and helper utilities.

The design target for v1 is practical reproducibility, not total emulator abstraction. `ps2env` owns lifecycle and RL-facing state. PCSX2 remains the execution backend and is driven through stable control surfaces already present in the vendored source tree.

## Design Principles

- `ps2env` is the source of truth for runtime state and user config.
- The game is paused outside explicit runtime loops.
- Startup, initialization, reset, and step all run through frame-bounded loops.
- The public API stays Gym-like, but lifecycle semantics are stricter than a generic emulator wrapper.
- Native PCSX2 settings are generated, not authored by hand in the main path.
- Environment authors extend behavior through Python modules, not emulator patches.

## Core Subsystems

PS2Env is composed of the following subsystems:

1. PCSX2  
   Emulator backend, launched from vendored [third_party/pcsx2](../third_party/pcsx2).
2. Xdummy  
   Headless X server providing a deterministic render target in containers.
3. X11/XTest input layer  
   Keyboard-driven emulator hotkeys for pause/resume and debug frame advance.
4. PINE IPC layer  
   Memory reads/writes, emulator status, and savestate slot control.
5. XShm capture layer  
   Final frame capture from the emulator render window.
6. `framelayer` / `framewait`  
   Native timing helpers used to count frames and block until requested frame deltas are observed.
7. `EnvContext`  
   Runtime facade exposing observation, counters, variables, actions, checks, callbacks, and helper operations.
8. Policies  
   User-defined init/reset/step behavior.
9. Checks and callbacks  
   User-defined pause-time evaluation and helper logic.
10. Variable manager  
    Cheat-table-backed game variables resolved through memory reads and writes.

## Runtime Invariants

- The emulator is paused whenever PS2Env is not inside a game loop.
- After `start()` completes successfully, the environment is at a deterministic non-episode state that satisfies `startup_check`.
- `init()` and `reset()` are loop-driven policy executions that must end in an episode-ready state satisfying `episode_check`.
- `step()` performs exactly one policy iteration and one frame-bounded run segment.
- Only PS2Env tracks env state. The training server does not mirror or reconstruct game state.

## Runtime State Model

### Stable States

| State | Meaning |
| --- | --- |
| `SHUTDOWN` | No emulator process and no active subsystems. Initial state and post-`kill()` state. |
| `INITIALIZATION` | Emulator is running, startup is complete, and the environment is preparing a playable episode state. This is the state returned by `start()`. |
| `EPISODE` | Playable RL state. `step()` is allowed only here. |
| `TERMINATED` | Episode ended due to environment/game terminal condition. |
| `TRUNCATED` | Episode ended due to an external limit such as step or frame budget. |

### Internal Transitional State

| State | Meaning |
| --- | --- |
| `STARTUP` | Internal-only boot subphase inside `start()`. The emulator is launching, services are attaching, and the runtime is waiting for `startup_check`. |

`STARTUP` is not returned to API callers. `start()` blocks until startup completes and then returns with the stable state `INITIALIZATION`.

## Public Method Guard Matrix

| Method | Allowed From | Result State | Notes |
| --- | --- | --- | --- |
| `start()` | `SHUTDOWN` | `INITIALIZATION` | Launches emulator, attaches subsystems, waits for `startup_check`. |
| `init()` | `INITIALIZATION` | `EPISODE` | Runs `init_policy` loop until `episode_check` is true. |
| `step()` | `EPISODE` | `EPISODE`, `TERMINATED`, or `TRUNCATED` | Executes exactly one step policy iteration and one frame-bounded run. |
| `reset()` | `EPISODE`, `TERMINATED`, `TRUNCATED` | `EPISODE` | Runs `reset_policy` loop until `episode_check` is true. |
| `kill()` | Any state | `SHUTDOWN` | Tears down emulator process and all attached subsystems. |

Any call made from an invalid state raises a state-guard error before touching the emulator.

## Lifecycle Semantics

### `start()`

`start()` performs the following sequence:

1. Create the env-scoped runtime directory structure.
2. Generate PCSX2 config files and hotkey bindings from `ps2env` TOML.
3. Start Xdummy and initialize input/capture dependencies.
4. Launch PCSX2 in portable mode with generated settings.
5. Attach frame and capture helpers.
6. Wait `startup_delay` seconds for process stabilization.
7. Enter the startup loop:
   - Resume emulator
   - Wait `lifecycle.frames_per_loop`
   - Capture frame
   - Pause emulator
   - Update context
   - Run `startup_check`
8. Exit when `startup_check` is true.
9. Set runtime state to `INITIALIZATION` and return.

`start()` does not use a policy. It only waits for the deterministic non-episode baseline requested by the environment config.

### `init()`

`init()` repeatedly runs `init_policy` in a game loop until `episode_check` becomes true.

Each loop iteration is:

1. `ctx.update(...)`
2. `init_policy.get_action(ctx, action=None)`
3. Resume emulator
4. `init_policy.take_action(ctx, resolved_action)`
5. Wait `lifecycle.frames_per_loop`
6. Capture frame
7. Pause emulator
8. `ctx.update(...)`
9. Run `episode_check`

When `episode_check` becomes true, state changes to `EPISODE`.

### `reset()`

`reset()` uses the same loop shape as `init()`, but runs `reset_policy`. It is policy-driven by default and does not implicitly load a savestate unless the environment author chooses to do so inside reset logic.

### `step()`

`step()` uses exactly one iteration of `step_policy` and one run window. The flow is fixed:

1. `policy/step_policy.py -> get_action(ctx, action)`
2. Resume game
3. `policy.take_action(ctx, resolved_action)`
4. Wait `stepping.n_frames_per_action`
5. Capture the final frame
6. Suspend game
7. `ctx.update(frame=..., frame_count=...)`
8. Increment `ctx.step_count`
9. Run `game.step_checks`
10. Compute reward
11. Update env state to `EPISODE`, `TERMINATED`, or `TRUNCATED`
12. Return Gym-style `(observation, reward, terminated, truncated, info)`

`FrameAdvance` exists only as a debug fallback. Production stepping uses resume, real-time frame waiting, capture, and pause.

## Native Frame-Advance Environment Slice

The implemented environment slice uses stock PCSX2 native frame advance as its deterministic stepping primitive.

For each requested frame:

1. Ensure the VM is paused.
2. Apply the current input state.
3. Send the configured frame-advance hotkey.
4. Poll PINE status until the VM returns to `paused`.
5. Increment the env-owned frame counter by exactly one.

`n_frames_per_step` and `frames_per_loop` are both implemented in terms of repeated single-frame native advances. No wall-clock sleeps are used while the game is advancing.

The env-owned `ctx.frame_count` is the authoritative runtime frame counter for v1 because stock PCSX2 does not expose its internal frame counter over PINE.

## Failure Handling

Any of the following are treated as hard runtime failures:

- PCSX2 launch failure
- PINE connection failure after startup
- Input hotkey delivery failure for pause/resume
- Capture window discovery failure
- Lifecycle timeout in startup, init, or reset loops
- Irrecoverable memory-variable decode failure

Hard runtime failures force best-effort suspension, then `kill()`, and leave the env in `SHUTDOWN`. Recovery is explicit: the caller must `start()` again.

## Control Plane Split

The v1 runtime intentionally uses multiple control surfaces because stock PCSX2 does not expose everything over one API.

| Capability | Control Surface | Reason |
| --- | --- | --- |
| Launch and settings | Generated CLI + generated `.ini` | Stable, reproducible, and supported by vendored PCSX2. |
| Pause / resume | X11/XTest keyboard hotkeys | Stock PINE does not expose pause/resume commands. |
| Debug frame advance | X11/XTest keyboard hotkeys | Stock PCSX2 hotkeys already expose `FrameAdvance`. |
| Memory reads / writes | PINE IPC | Native support exists in vendored PCSX2. |
| Savestate save / load | PINE IPC | Native slot-based save/load exists in vendored PCSX2. |
| Emulator status | PINE IPC | Native `Running`, `Paused`, and `Shutdown` status is exposed. |
| Final frame capture | XShm | Efficient capture of the rendered frame from the X11 surface. |
| Frame counting / waits | `framelayer` + `framewait` | Keeps stepping aligned with rendered frame progression. |

### Hotkey Control

PS2Env generates a dedicated PCSX2 hotkey profile and owns the bindings for:

- `TogglePause`
- `FrameAdvance`

These are keyboard-only bindings sent through X11/XTest. The exact generated keys are internal runtime details and must not overlap with user game bindings.

## Configuration Model

### Source of Truth

The public configuration format is TOML. Native PCSX2 `.ini` files are implementation artifacts derived from TOML and written into the env runtime directory.

Configuration generation order is:

1. PS2Env defaults
2. User TOML values
3. Optional advanced native `.ini` overrides
4. Hard runtime invariants

Hard runtime invariants always win. Users may not override settings that break deterministic stepping or required control-plane features.

### Config Example

```toml
[game]
game_path = "/games/example.iso"
startup_check = "check_start_menu"
episode_check = "check_episode"
step_checks = ["win_lose", "step_limit"]
checks_dir = "checks"
callbacks_dir = "callbacks"
policy_dir = "policy"
env_utils = "env_utils"

[boot]
startup_delay = 30.0
fastboot = true
game_args = []

[display]
resolution = [640, 360]
display_offset = 99

[capture]
game_fps = 60
observation_shape = [320, 180]

[stepping]
n_frames_per_action = 4
after_action = "hold"
capture_action = false

[lifecycle]
frames_per_loop = 4
timeout_frames = 1440

[input]
actions_dir = "actions"
game_actions = ["combat", "move"]

[vision]
checkpoint = "path/to/mobilenet.onnx"
labels = ["game", "player", "start_menu", "team"]
input_size = [224, 224]

[pcsx2]
renderer = "vulkan"
portable = true
batch = true
nogui = true
enable_pine = true
pine_slot = 28011
extra_cli_args = []

[pcsx2.ini_overrides]
"UI.StartPaused" = true
"UI.PauseOnFocusLoss" = false

[savestates]
startup = 1
reset = 2
scratch = 10

[variables]
ct_path = "path/to/cheattable.CT"
allow_writes = true
majority_vote = true
```

### Key Config Decisions

- Replace ambiguous `[game].args` with `[boot].game_args`.
- Use `[pcsx2].extra_cli_args` only as an escape hatch for advanced emulator flags.
- Use `[pcsx2.ini_overrides]` for advanced native overrides. YAML is not used.
- Use `[savestates]` for named checkpoints.
- Use `[variables]` for runtime memory-variable configuration.

### Required Generated PCSX2 Settings

PS2Env must generate native settings that enforce:

- Vulkan renderer
- Pause on start enabled
- Pause on focus loss disabled
- PINE enabled with a deterministic slot
- Portable mode enabled
- Automatic resume-state save on shutdown disabled
- Achievements hardcore mode disabled

These are required because pause/resume control, frame advance, capture stability, and deterministic container execution depend on them.

### CLI Model

The generated PCSX2 launch command is conceptually:

```bash
pcsx2-qt -batch -nogui -portable -fastboot /path/to/game.iso
```

PS2 program args from `[boot].game_args` are translated into the native PCSX2 `-gameargs` string. Advanced emulator flags from `[pcsx2].extra_cli_args` are appended only after validation. Conflicting flags are rejected.

## EnvContext Contract

`EnvContext` is the runtime object passed into policies, checks, and callbacks. It is the only supported facade for environment-defined code.

At minimum, `EnvContext` exposes:

- Current observation and raw frame
- Current emulator frame count
- Current env step count
- Current stable env state
- Variable manager access
- Vision model access
- Loaded checks and callbacks
- Base action helpers
- Savestate helpers
- Emulator control helpers such as capture, pause-state assertions, and context updates

`ctx.update(frame=..., frame_count=...)` refreshes:

- `ctx.observation`
- `ctx.frame`
- `ctx.frame_count`
- `ctx.variables`
- Any derived env metadata cached by callbacks or helpers

## Policy, Check, and Callback Contracts

### Policy Files

Each environment must define:

- `policy/init_policy.py`
- `policy/reset_policy.py`
- `policy/step_policy.py`

Each module must export exactly one concrete subclass of `Policy`. Multiple concrete subclasses are a configuration error.

### Policy Interface

```python
class Policy:
    def get_action(self, ctx, action=None):
        raise NotImplementedError

    def take_action(self, ctx, resolved_action):
        raise NotImplementedError
```

Rules:

- `init_policy.get_action(ctx, action=None)` does not consume a Gym action.
- `reset_policy.get_action(ctx, action=None)` does not consume a Gym action.
- `step_policy.get_action(ctx, action)` converts the Gym action into a resolved internal action.
- `take_action()` performs the emulator input side effects.

Policies have access through `ctx` to:

- callbacks
- checks
- helper utilities
- variable access
- base actions
- savestate helpers

### Checks

Checks are functions loaded from `checks_dir`. Each named check returns:

```python
(result: bool, info: dict)
```

Rules:

- Checks always run while the emulator is paused.
- Checks may inspect the latest observation, variables, or vision outputs.
- `info["terminated"] = True` marks a terminal end.
- `info["truncated"] = True` marks a truncated end.
- The raw `result` value is retained for diagnostics and reward logic, but only `terminated` and `truncated` drive lifecycle state transitions.

### Callbacks

Callbacks are helper callables loaded from `callbacks_dir` and exposed through `ctx`. In v1 they are not framework-managed lifecycle hooks. They are plain utilities invoked explicitly by policies, checks, or reward logic.

## Implemented Loader Rules

- Check modules must expose `check(ctx) -> (bool, dict)`.
- Callback modules may expose `callback(ctx)`, `step(ctx)`, or a function named after the module stem.
- Policy modules must define exactly one concrete `Policy` subclass.
- Reward modules may expose `compute_reward(ctx, info)` or `compute(ctx, info)`.

## Reward Contract

Reward is computed after checks run and after `ctx.step_count` increments.

In v1, reward is environment-defined helper logic loaded from `env_utils`. If the environment does not provide reward logic, the default reward is `0.0`.

The reward function consumes:

- `ctx`
- current check results
- accumulated per-step `info`

## Savestates

### Public Model

Savestates are exposed publicly as named checkpoints, not raw slots.

Default checkpoint mapping:

| Checkpoint | Slot |
| --- | --- |
| `startup` | 1 |
| `reset` | 2 |
| `scratch` | 10 |

Framework-reserved slots are:

- `1` for `startup`
- `2` for `reset`
- `10` for `scratch`

Slots `3` through `9` remain available for environment-defined advanced use.

### Backing Behavior

- PS2Env saves and loads checkpoints through PCSX2 slot-based savestate operations over PINE.
- Reset remains policy-driven by default.
- Policies may explicitly call checkpoint helpers for fast reset or debugging workflows.
- Save/load operations are synchronized with the paused runtime boundary.

## Game Variables

### Source

Game variables are loaded from the Cheat Engine `.CT` file configured in `[variables].ct_path`.

### Variable Resolution Rules

- Group descriptions become variable names.
- Child entries inside a group become backing addresses for that variable.
- A variable may have multiple backing addresses.
- Reads are performed through PINE memory access.
- Majority voting is used when multiple addresses represent the same logical value.

### Read and Write Behavior

- Read: all backing addresses are sampled, converted to the configured type, and majority-voted.
- Tie: if no majority exists, the runtime uses the last stable value if one exists; otherwise it raises a variable read error.
- Write: the same value is written to every backing address for the variable.

This keeps duplicated cheat-table entries useful as a robustness mechanism instead of redundant noise.

## Tools

PS2Env includes four quality-of-life tool classes:

1. Cheat Engine companion tool  
   Runs the game plus cheat-table inspection UI for variable discovery and debugging.
2. Record tool  
   Runs the game interactively, records frames and inputs, and stores replay data.
3. Analyze tool  
   Replays recorded data frame by frame, overlays actions, and saves labeled classifier frames.
4. Train tool  
   Trains the small ONNX-compatible vision classifier used by checks.

These tools are not part of the core stepping loop, but they are first-class product features and must share the same config model where possible.

## Deployment Model

### Container Shape

Production deployment for the solved bring-up slice is many GPU-scoped Linux containers per machine.

Container assumptions:

- NVIDIA runtime available
- Vulkan libraries available
- Xdummy available
- X11 utilities available
- XShm available
- PCSX2 launched inside the container against the Xdummy display

The provided CUDA/X11/Vulkan Dockerfile template is the baseline dependency model. PS2Env reuses the graphics and X server structure, but targets PCSX2 instead of Proton/Wine.
The current bring-up image also includes a runtime installer for host-matching NVIDIA display userspace, mirroring the proven pattern from the Windows-game environment.

### Process Layout

Per worker container:

- One NVIDIA GPU is made visible to the container.
- The Python runtime launches one worker process.
- That worker uses:
  - display `:90`
  - PINE slot `28011`
  - its own output directory
- The worker stages a private portable PCSX2 tree from the extracted image payload and writes its own generated config there.
- The worker starts one Xdummy display.
- The worker launches one PCSX2 instance with Vulkan.
- The worker discovers its render window.
- FFmpeg captures the worker’s X11 display to `smoke.mp4` for manual validation.
- X11/XTest sends hotkeys and random smoke actions.
- PINE provides memory/status/savestate IPC.

At the host orchestration layer:

- Worker `i` is assigned to host GPU `i % gpu_count`.
- The host launches one container per worker with `--gpus device=<gpu_index>`.
- The host runner may optionally restrict the scheduling pool to a known-good subset of GPUs.

### Distributed Layout

- Rollout server: runs game containers and exposes env lifecycle RPCs
- Training server: orchestrates `start`, `init`, `step`, `reset`, and `kill`

The rollout server is executor-only. It does not own RL state beyond the live env instance it is hosting.

## Validation Targets

The implementation must validate:

- state guards for every public method
- startup reaching `startup_check`
- init/reset reaching `episode_check`
- exact frame count behavior for `step()`
- pause/resume hotkey delivery in Xdummy
- XShm capture stability under `-batch -nogui`
- PINE connectivity and slot-based savestate control
- checkpoint mapping correctness
- cheat-table majority-vote variable resolution
- Vulkan enforcement in generated PCSX2 settings

## Implemented Bring-Up Notes

The first runtime slice intentionally stops short of deterministic Gym stepping. It validates the lower-level prerequisites first:

- PCSX2 can run inside Docker with Vulkan
- multiple workers can coexist in one container
- each worker gets isolated display/input/IPC state
- the game renders into a virtual monitor
- keyboard-driven controller input reaches the game
- artifact capture and logging are reliable enough for manual inspection

The smoke runtime currently requires a BIOS directory mount. The worker auto-selects the first plausible BIOS image in that directory unless a specific BIOS filename is configured.
The current implementation scans recursively and prefers `.BIN` BIOS images when multiple candidates are present.

The smoke runtime does not yet use XShm. It records the full worker display via FFmpeg/X11 capture because the immediate goal is bring-up verification, not per-step observation extraction.

## Resolved Bring-Up Result

The current implementation now successfully reaches:

- Xdummy startup
- PCSX2 process launch
- BIOS load
- ISO load
- PINE availability
- Vulkan GS initialization
- render window creation
- FFmpeg recording
- unpause hotkey delivery
- random action injection

The critical deployment constraint discovered during debugging is:

- With PCSX2 + Vulkan + Xdummy, a container that sees multiple GPUs does not offer a presentable Vulkan surface on every visible GPU.
- A single-GPU-visible container does work.
- Therefore round-robin scheduling is implemented at the container level, not by exposing all GPUs to one shared worker container.
