#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

PCSX2_APPIMAGE=""
IMAGE_TAG="ps2env-smoke:latest"

usage() {
  cat <<'EOF'
Usage:
  scripts/build-image.sh --pcsx2-appimage <path> [--tag <tag>]

Options:
  --pcsx2-appimage  Host path to the PCSX2 AppImage to bake into the image.
  --tag             Docker image tag. Default: ps2env-smoke:latest
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pcsx2-appimage)
      PCSX2_APPIMAGE="$2"
      shift 2
      ;;
    --tag)
      IMAGE_TAG="$2"
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

if [[ -z "$PCSX2_APPIMAGE" ]]; then
  echo "--pcsx2-appimage is required" >&2
  usage >&2
  exit 1
fi

PCSX2_APPIMAGE=$(realpath "$PCSX2_APPIMAGE")
if [[ ! -f "$PCSX2_APPIMAGE" ]]; then
  echo "PCSX2 AppImage not found: $PCSX2_APPIMAGE" >&2
  exit 1
fi

BUILD_CONTEXT=$(mktemp -d)
cleanup() {
  rm -rf "$BUILD_CONTEXT"
}
trap cleanup EXIT

mkdir -p "$BUILD_CONTEXT"
tar \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='build' \
  --exclude='dist' \
  --exclude='output' \
  --exclude='third_party' \
  --exclude='docs' \
  -C "$REPO_ROOT" \
  -cf - . | tar -C "$BUILD_CONTEXT" -xf -

cp "$PCSX2_APPIMAGE" "$BUILD_CONTEXT/pcsx2.AppImage"
chmod +x "$BUILD_CONTEXT/pcsx2.AppImage"

echo "Building Docker image: $IMAGE_TAG"
echo "Using PCSX2 AppImage: $PCSX2_APPIMAGE"
docker build -t "$IMAGE_TAG" "$BUILD_CONTEXT"

