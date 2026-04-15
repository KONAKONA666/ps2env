# PS2Env Architecture Plan

## Goal

Build a reproducible PS2 RL environment framework that:

- wraps PCSX2 behind a Gym-style API
- runs reliably inside GPU-enabled Linux containers
- supports deterministic pause-bounded stepping
- lets environment authors define policies, checks, callbacks, and variables in Python

## Architecture Decisions

### 1. Configuration Ownership

- `ps2env` TOML is the only primary config format.
- Native PCSX2 `.ini` files are generated from TOML.
- Native CLI and `.ini` overrides remain available only as advanced escape hatches.

### 2. Emulator Reference

- Vendored [third_party/pcsx2](../third_party/pcsx2) is the reference backend for v1.
- v1 does not require a custom PCSX2 fork.
- Existing PCSX2 features are reused where possible:
  - PINE for memory, status, and savestates
  - PCSX2 hotkeys for pause and frame advance
  - native renderer selection and portable-mode config

### 3. Runtime Control Model

- The emulator is paused outside explicit loops.
- `start()` owns startup synchronization.
- `init()` and `reset()` are policy-driven loops.
- `step()` is a single fixed loop iteration.
- Frame advance exists only for debugging, not as the main step implementation.

### 4. Public Lifecycle

Stable runtime states:

- `SHUTDOWN`
- `INITIALIZATION`
- `EPISODE`
- `TERMINATED`
- `TRUNCATED`

Internal runtime state:

- `STARTUP`

Method contract:

- `start`: `SHUTDOWN -> INITIALIZATION`
- `init`: `INITIALIZATION -> EPISODE`
- `step`: `EPISODE -> EPISODE | TERMINATED | TRUNCATED`
- `reset`: `EPISODE | TERMINATED | TRUNCATED -> EPISODE`
- `kill`: `any -> SHUTDOWN`

### 5. Savestate Model

- Public model uses named checkpoints.
- Framework-reserved slots:
  - `startup=1`
  - `reset=2`
  - `scratch=10`
- Raw slots remain an advanced tool, not the default public abstraction.

### 6. Variable Model

- Variables are sourced from Cheat Engine `.CT` files.
- Duplicate addresses are used for majority-vote reads.
- Writes fan out to every backing address in the variable group.

### 7. Deployment Model

- Bring-up slice: one GPU-visible worker container per scheduled worker.
- Later RL runtime: the worker remains the unit of env state ownership.
- Vulkan is the required renderer in Linux/Docker.
- Xdummy provides the headless X server.
- Final architecture targets XShm for observations.
- Implemented smoke slice records the X11 display with FFmpeg for manual validation.
- Rollout servers execute envs.
- Training servers own RL orchestration.

## Subsystem Boundaries

### Launcher and Config Generation

Responsible for:

- parsing TOML
- generating native PCSX2 config
- validating required invariants
- building the emulator command line

### Emulator Control

Responsible for:

- starting and stopping PCSX2
- tracking runtime directories
- ensuring pause/resume and startup readiness

### Display, Input, and Capture

Responsible for:

- Xdummy session management
- render window discovery
- X11/XTest hotkey injection
- XShm capture
- frame wait/count integration

### Env Runtime

Responsible for:

- state machine enforcement
- lifecycle method guards
- `EnvContext`
- Gym-style return values

### Policy / Check / Callback Loader

Responsible for:

- Python module loading
- contract validation
- env-defined helper discovery

### Savestate and Variable Manager

Responsible for:

- named checkpoint mapping
- PINE-backed save/load
- cheat-table parsing
- majority-vote reads and broadcast writes

### Tooling

Responsible for:

- cheat-table debugging
- gameplay recording
- frame analysis and labeling
- classifier training

### Rollout and Deploy Hardening

Responsible for:

- container image shape
- runtime health checks
- remote env execution model
- rollout/training boundary

## Delivery Phases

### Phase 1: Core Runtime

- launcher
- config generation
- process management
- X11 input and capture primitives
- state machine

### Phase 2: Environment Authoring Surface

- `EnvContext`
- base actions
- policies
- checks
- callbacks
- reward wiring

### Phase 3: Persistence and Introspection

- savestates
- checkpoints
- game variables
- debug helpers

### Phase 4: Tools

- record
- analyze
- train
- cheat companion workflow

### Phase 5: Deploy Hardening

- Docker image
- rollout server outline
- health checks
- reproducibility validation

## Acceptance Targets

- Deterministic public lifecycle enforcement
- Stable capture and stepping on Linux/Xdummy/Vulkan
- No hidden env state outside the live env instance
- Python-first environment authoring surface
- Native PCSX2 control without requiring a fork
