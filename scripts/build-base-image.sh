#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

BASE_TAG="ps2env-base:latest"
PCSX2_APPIMAGE=""
FORCE_APPIMAGE_BUILD=0

usage() {
  cat <<'EOF'
Usage:
  scripts/build-base-image.sh [--tag <tag>] [--pcsx2-appimage <path>] [--force-appimage-build]

Options:
  --tag                  Base image tag. Default: ps2env-base:latest
  --pcsx2-appimage       Use an existing PCSX2 AppImage instead of building one from vendored source.
  --force-appimage-build Force a rebuild of the vendored PCSX2 AppImage before building the base image.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      BASE_TAG="$2"
      shift 2
      ;;
    --pcsx2-appimage)
      PCSX2_APPIMAGE="$2"
      shift 2
      ;;
    --force-appimage-build)
      FORCE_APPIMAGE_BUILD=1
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

if [[ -n "$PCSX2_APPIMAGE" ]]; then
  PCSX2_APPIMAGE=$(realpath "$PCSX2_APPIMAGE")
  if [[ ! -f "$PCSX2_APPIMAGE" ]]; then
    echo "PCSX2 AppImage not found: $PCSX2_APPIMAGE" >&2
    exit 1
  fi
else
  appimage_args=()
  if [[ "$FORCE_APPIMAGE_BUILD" == "1" ]]; then
    appimage_args+=(--force)
  fi
  "$SCRIPT_DIR/build-pcsx2-appimage.sh" "${appimage_args[@]}"
  PCSX2_APPIMAGE=$(realpath "$REPO_ROOT/build/pcsx2/pcsx2-qt.AppImage")
fi

BUILD_CONTEXT=$(mktemp -d)
cleanup() {
  rm -rf "$BUILD_CONTEXT"
}
trap cleanup EXIT

mkdir -p "$BUILD_CONTEXT/scripts" "$BUILD_CONTEXT/third_party"
cp "$REPO_ROOT/Dockerfile.base" "$BUILD_CONTEXT/Dockerfile.base"
cp "$REPO_ROOT/scripts/install-nvidia-display-driver.sh" "$BUILD_CONTEXT/scripts/install-nvidia-display-driver.sh"
cp "$PCSX2_APPIMAGE" "$BUILD_CONTEXT/pcsx2.AppImage"
chmod +x "$BUILD_CONTEXT/pcsx2.AppImage" "$BUILD_CONTEXT/scripts/install-nvidia-display-driver.sh"
cp -a "$REPO_ROOT/third_party/pcsx2" "$BUILD_CONTEXT/third_party/pcsx2"

echo "Building base image: $BASE_TAG"
echo "Using PCSX2 AppImage: $PCSX2_APPIMAGE"
docker build -f "$BUILD_CONTEXT/Dockerfile.base" -t "$BASE_TAG" "$BUILD_CONTEXT"
