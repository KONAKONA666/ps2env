#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="ps2env-smoke:latest"
GAME_PATH=""
BIOS_DIR="${PS2ENV_BIOS_DIR:-}"
WORKERS="1"
DURATION_SECONDS="30"
OUTPUT_DIR=""
RUN_ID="run-$(date -u +%Y%m%dT%H%M%SZ)"
CACHE_DIR="${PS2ENV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/ps2env}"

usage() {
  cat <<'EOF'
Usage:
  scripts/run-game.sh --game <iso> --workers <n> --duration-seconds <n> --output-dir <dir> [--bios-dir <dir>] [--image <tag>]

Environment:
  PS2ENV_BIOS_DIR  Optional default BIOS directory if --bios-dir is not provided.
  PS2ENV_GPU_LIST  Optional comma-separated host GPU indices to use instead of all GPUs from nvidia-smi.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --game)
      GAME_PATH="$2"
      shift 2
      ;;
    --bios-dir)
      BIOS_DIR="$2"
      shift 2
      ;;
    --workers)
      WORKERS="$2"
      shift 2
      ;;
    --duration-seconds)
      DURATION_SECONDS="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --image)
      IMAGE_TAG="$2"
      shift 2
      ;;
    --run-id)
      RUN_ID="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$GAME_PATH" || -z "$OUTPUT_DIR" ]]; then
  echo "--game and --output-dir are required" >&2
  usage >&2
  exit 1
fi

if [[ -z "$BIOS_DIR" ]]; then
  echo "A BIOS directory is required. Pass --bios-dir or set PS2ENV_BIOS_DIR." >&2
  exit 1
fi

GAME_PATH=$(realpath "$GAME_PATH")
OUTPUT_DIR=$(realpath -m "$OUTPUT_DIR")
BIOS_DIR=$(realpath "$BIOS_DIR")

if [[ ! -f "$GAME_PATH" ]]; then
  echo "Game ISO not found: $GAME_PATH" >&2
  exit 1
fi

if [[ ! -d "$BIOS_DIR" ]]; then
  echo "BIOS directory not found: $BIOS_DIR" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
mkdir -p "$CACHE_DIR"

echo "Running smoke runtime"
echo "  Image:      $IMAGE_TAG"
echo "  Game:       $GAME_PATH"
echo "  BIOS dir:   $BIOS_DIR"
echo "  Workers:    $WORKERS"
echo "  Duration:   $DURATION_SECONDS"
echo "  Output dir: $OUTPUT_DIR/$RUN_ID"
echo "  Cache dir:  $CACHE_DIR"

if [[ -n "${PS2ENV_GPU_LIST:-}" ]]; then
  IFS=',' read -r -a GPU_INDICES <<<"${PS2ENV_GPU_LIST}"
  for idx in "${!GPU_INDICES[@]}"; do
    GPU_INDICES[$idx]="${GPU_INDICES[$idx]//[[:space:]]/}"
  done
else
  mapfile -t GPU_INDICES < <(nvidia-smi --query-gpu=index --format=csv,noheader | tr -d '[:space:]')
fi
if [[ "${#GPU_INDICES[@]}" -eq 0 ]]; then
  echo "No NVIDIA GPUs were detected by nvidia-smi on the host." >&2
  exit 1
fi

RUN_ROOT="$OUTPUT_DIR/$RUN_ID"
mkdir -p "$RUN_ROOT"

declare -a CONTAINER_PIDS=()
declare -a CONTAINER_NAMES=()
declare -a CONTAINER_LOGS=()

for (( worker_id=0; worker_id<WORKERS; worker_id++ )); do
  gpu_index="${GPU_INDICES[$((worker_id % ${#GPU_INDICES[@]}))]}"
  worker_label=$(printf '%02d' "$worker_id")
  container_name="ps2env-smoke-${RUN_ID//[^a-zA-Z0-9_.-]/-}-w${worker_label}"
  container_log="$RUN_ROOT/container-worker-${worker_label}.log"

  echo "  Worker ${worker_label} -> GPU ${gpu_index}"

  docker run --rm \
    --name "$container_name" \
    --gpus "device=${gpu_index}" \
    --shm-size=2g \
    --cap-add=SYS_PTRACE \
    --security-opt seccomp=unconfined \
    -e "PS2ENV_HOST_UID=$(id -u)" \
    -e "PS2ENV_HOST_GID=$(id -g)" \
    -e "PS2ENV_INSTALL_NVIDIA_DISPLAY_DRIVER=1" \
    -e "PS2ENV_CACHE_DIR=/workspace/cache" \
    -v "$GAME_PATH:/workspace/game/game.iso:ro" \
    -v "$BIOS_DIR:/workspace/bios:ro" \
    -v "$OUTPUT_DIR:/workspace/output" \
    -v "$CACHE_DIR:/workspace/cache" \
    "$IMAGE_TAG" \
    --config /opt/ps2env/configs/smoke.toml \
    --workers 1 \
    --worker-id-base "$worker_id" \
    --duration-seconds "$DURATION_SECONDS" \
    --output-root /workspace/output \
    --run-id "$RUN_ID" \
    >"$container_log" 2>&1 &

  CONTAINER_PIDS+=("$!")
  CONTAINER_NAMES+=("$container_name")
  CONTAINER_LOGS+=("$container_log")
done

overall_status=0
for idx in "${!CONTAINER_PIDS[@]}"; do
  pid="${CONTAINER_PIDS[$idx]}"
  if ! wait "$pid"; then
    overall_status=1
    echo "Container ${CONTAINER_NAMES[$idx]} failed. See ${CONTAINER_LOGS[$idx]}" >&2
  fi
done

exit "$overall_status"
