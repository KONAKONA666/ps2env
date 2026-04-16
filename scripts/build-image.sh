#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

BASE_TAG="ps2env-base:latest"
GAME_TAG="ps2env-smoke:latest"
PCSX2_APPIMAGE=""
GAME_ISO=""
BIOS_DIR=""
BASELINE_STATE=""
REBUILD_BASE=0

usage() {
  cat <<'EOF'
Usage:
  scripts/build-image.sh --game-iso <path> --bios-dir <path> --baseline-state <path> [--tag <tag>] [--base-tag <tag>] [--pcsx2-appimage <path>] [--rebuild-base]

Options:
  --game-iso         Host path to the PS2 ISO to bake into the game image.
  --bios-dir         Host path to the BIOS directory to bake into the game image.
  --baseline-state   Host path to the baseline .p2s file to bake into the game image.
  --pcsx2-appimage   Optional prebuilt PCSX2 AppImage for the base image.
  --base-tag         Base image tag. Default: ps2env-base:latest
  --tag              Game image tag. Default: ps2env-smoke:latest
  --rebuild-base     Rebuild the base image even if a matching local tag already exists.
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
    --pcsx2-appimage)
      PCSX2_APPIMAGE="$2"
      shift 2
      ;;
    --base-tag)
      BASE_TAG="$2"
      shift 2
      ;;
    --tag)
      GAME_TAG="$2"
      shift 2
      ;;
    --rebuild-base)
      REBUILD_BASE=1
      shift
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

base_args=(--tag "$BASE_TAG")
if [[ -n "$PCSX2_APPIMAGE" ]]; then
  base_args+=(--pcsx2-appimage "$PCSX2_APPIMAGE")
  REBUILD_BASE=1
fi

if [[ "$REBUILD_BASE" == "1" ]] || ! docker image inspect "$BASE_TAG" >/dev/null 2>&1; then
  "$SCRIPT_DIR/build-base-image.sh" "${base_args[@]}"
else
  echo "Reusing existing base image: $BASE_TAG"
fi

"$SCRIPT_DIR/build-game-image.sh" \
  --base-image "$BASE_TAG" \
  --tag "$GAME_TAG" \
  --game-iso "$GAME_ISO" \
  --bios-dir "$BIOS_DIR" \
  --baseline-state "$BASELINE_STATE"
