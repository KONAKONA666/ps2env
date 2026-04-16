#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

BASE_IMAGE="ps2env-base:latest"
GAME_TAG="ps2env-smoke:latest"
GAME_ISO=""
BIOS_DIR=""
BASELINE_STATE=""

usage() {
  cat <<'EOF'
Usage:
  scripts/build-game-image.sh --game-iso <path> --bios-dir <path> --baseline-state <path> [--base-image <tag>] [--tag <tag>]

Options:
  --game-iso        Host path to the PS2 ISO to bake into the image.
  --bios-dir        Host path to the BIOS directory to bake into the image.
  --baseline-state  Host path to the baseline .p2s file to bake into the image.
  --base-image      Base image tag. Default: ps2env-base:latest
  --tag             Game image tag. Default: ps2env-smoke:latest
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --game-iso)
      GAME_ISO="$2"
      shift 2
      ;;
    --bios-dir)
      BIOS_DIR="$2"
      shift 2
      ;;
    --baseline-state)
      BASELINE_STATE="$2"
      shift 2
      ;;
    --base-image)
      BASE_IMAGE="$2"
      shift 2
      ;;
    --tag)
      GAME_TAG="$2"
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

if [[ -z "$GAME_ISO" || -z "$BIOS_DIR" || -z "$BASELINE_STATE" ]]; then
  echo "--game-iso, --bios-dir, and --baseline-state are required" >&2
  usage >&2
  exit 1
fi

GAME_ISO=$(realpath "$GAME_ISO")
BIOS_DIR=$(realpath "$BIOS_DIR")
BASELINE_STATE=$(realpath "$BASELINE_STATE")

if [[ ! -f "$GAME_ISO" ]]; then
  echo "Game ISO not found: $GAME_ISO" >&2
  exit 1
fi
if [[ ! -d "$BIOS_DIR" ]]; then
  echo "BIOS directory not found: $BIOS_DIR" >&2
  exit 1
fi
if [[ ! -f "$BASELINE_STATE" ]]; then
  echo "Baseline state not found: $BASELINE_STATE" >&2
  exit 1
fi

BUILD_CONTEXT=$(mktemp -d)
cleanup() {
  rm -rf "$BUILD_CONTEXT"
}
trap cleanup EXIT

mkdir -p "$BUILD_CONTEXT/_game_assets/bios" "$BUILD_CONTEXT/_game_assets/sstates/baseline" "$BUILD_CONTEXT/docker"
cp "$REPO_ROOT/Dockerfile.game" "$BUILD_CONTEXT/Dockerfile.game"
cp "$REPO_ROOT/pyproject.toml" "$BUILD_CONTEXT/pyproject.toml"
cp -a "$REPO_ROOT/ps2env" "$BUILD_CONTEXT/ps2env"
cp -a "$REPO_ROOT/user_env" "$BUILD_CONTEXT/user_env"
cp -a "$REPO_ROOT/configs" "$BUILD_CONTEXT/configs"
cp "$REPO_ROOT/docker/entrypoint.sh" "$BUILD_CONTEXT/docker/entrypoint.sh"
cp "$GAME_ISO" "$BUILD_CONTEXT/_game_assets/game.iso"
cp -a "$BIOS_DIR/." "$BUILD_CONTEXT/_game_assets/bios/"
cp "$BASELINE_STATE" "$BUILD_CONTEXT/_game_assets/sstates/baseline/episode_start.p2s"
chmod +x "$BUILD_CONTEXT/docker/entrypoint.sh"

echo "Building game image: $GAME_TAG"
echo "Using base image: $BASE_IMAGE"
docker build \
  -f "$BUILD_CONTEXT/Dockerfile.game" \
  --build-arg "BASE_IMAGE=$BASE_IMAGE" \
  -t "$GAME_TAG" \
  "$BUILD_CONTEXT"
