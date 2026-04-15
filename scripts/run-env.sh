#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="ps2env-smoke:latest"
CONFIG_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/configs/env_basic.toml"
GAME_PATH=""
BIOS_DIR="${PS2ENV_BIOS_DIR:-}"
WORKERS="1"
STEPS="8"
OUTPUT_DIR=""
RUN_ID="env-$(date -u +%Y%m%dT%H%M%SZ)"
CACHE_DIR="${PS2ENV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/ps2env}"

usage() {
  cat <<'EOF'
Usage:
  scripts/run-env.sh --game <iso> --bios-dir <dir> --output-dir <dir> [--config <path>] [--workers <n>] [--steps <n>] [--image <tag>]

Environment:
  PS2ENV_BIOS_DIR  Optional default BIOS directory if --bios-dir is not provided.
  PS2ENV_GPU_LIST  Optional comma-separated host GPU indices. The first entry is used for the shared-container env runtime.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_PATH="$2"
      shift 2
      ;;
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
    --steps)
      STEPS="$2"
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

CONFIG_PATH=$(realpath "$CONFIG_PATH")
GAME_PATH=$(realpath "$GAME_PATH")
OUTPUT_DIR=$(realpath -m "$OUTPUT_DIR")
BIOS_DIR=$(realpath "$BIOS_DIR")
mkdir -p "$OUTPUT_DIR" "$CACHE_DIR"

CONFIG_ROOT="$(dirname "$CONFIG_PATH")"
SEARCH_DIR="$CONFIG_ROOT"
while [[ "$SEARCH_DIR" != "/" ]]; do
  if [[ -f "$SEARCH_DIR/pyproject.toml" ]]; then
    CONFIG_ROOT="$SEARCH_DIR"
    break
  fi
  SEARCH_DIR="$(dirname "$SEARCH_DIR")"
done
CONFIG_REL="${CONFIG_PATH#$CONFIG_ROOT/}"
CONTAINER_CONFIG_ROOT="/workspace/config_root"
CONTAINER_CONFIG="${CONTAINER_CONFIG_ROOT}/${CONFIG_REL}"

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

GPU_INDEX="${GPU_INDICES[0]}"
RUN_ROOT="$OUTPUT_DIR/$RUN_ID"
mkdir -p "$RUN_ROOT"
CONTAINER_LOG="$RUN_ROOT/container-env.log"

echo "Running PS2Env runtime"
echo "  Image:      $IMAGE_TAG"
echo "  Config:     $CONFIG_PATH"
echo "  Game:       $GAME_PATH"
echo "  BIOS dir:   $BIOS_DIR"
echo "  Workers:    $WORKERS"
echo "  Steps:      $STEPS"
echo "  Output dir: $RUN_ROOT"
echo "  Cache dir:  $CACHE_DIR"
echo "  GPU:        $GPU_INDEX"
echo "  Config root: $CONFIG_ROOT"

docker run --rm \
  --name "ps2env-env-${RUN_ID//[^a-zA-Z0-9_.-]/-}" \
  --gpus "device=${GPU_INDEX}" \
  --shm-size=2g \
  --cap-add=SYS_PTRACE \
  --security-opt seccomp=unconfined \
  -e "HOST_UID=$(id -u)" \
  -e "HOST_GID=$(id -g)" \
  -e "PS2ENV_HOST_UID=$(id -u)" \
  -e "PS2ENV_HOST_GID=$(id -g)" \
  -e "PS2ENV_INSTALL_NVIDIA_DISPLAY_DRIVER=1" \
  -e "PS2ENV_CACHE_DIR=/workspace/cache" \
  -v "$CONFIG_ROOT:$CONTAINER_CONFIG_ROOT:ro" \
  -v "$GAME_PATH:/workspace/game/game.iso:ro" \
  -v "$BIOS_DIR:/workspace/bios:ro" \
  -v "$OUTPUT_DIR:/workspace/output" \
  -v "$CACHE_DIR:/workspace/cache" \
  --entrypoint bash \
  "$IMAGE_TAG" \
  -lc "/opt/ps2env/install-nvidia-display-driver.sh && python3 -m ps2env.env_runtime --config $(printf '%q' "$CONTAINER_CONFIG") --workers $(printf '%q' "$WORKERS") --steps $(printf '%q' "$STEPS") --output-root /workspace/output --run-id $(printf '%q' "$RUN_ID") --game $(printf '%q' /workspace/game/game.iso) --bios-dir $(printf '%q' /workspace/bios); status=\$?; chown -R \"\$HOST_UID:\$HOST_GID\" /workspace/output || true; exit \$status" \
  >"$CONTAINER_LOG" 2>&1
