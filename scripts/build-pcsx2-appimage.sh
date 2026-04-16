#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
PCSX2_ROOT="$REPO_ROOT/third_party/pcsx2"

OUTPUT_PATH="$REPO_ROOT/build/pcsx2/pcsx2-qt.AppImage"
BUILD_ROOT="$REPO_ROOT/build/pcsx2-appimage"
BUILD_DIR="$BUILD_ROOT/build"
DEPS_DIR="${PS2ENV_PCSX2_DEPS_DIR:-$HOME/deps}"
PATCHES_URL="${PS2ENV_PCSX2_PATCHES_URL:-https://github.com/PCSX2/pcsx2_patches/releases/latest/download}"
JOBS="${PS2ENV_PCSX2_JOBS:-4}"
FORCE=0
INSIDE_DOCKER=0

usage() {
  cat <<'EOF'
Usage:
  scripts/build-pcsx2-appimage.sh [--output <path>] [--deps-dir <path>] [--jobs <n>] [--force]

Options:
  --output    AppImage output path. Default: build/pcsx2/pcsx2-qt.AppImage
  --deps-dir  Dependency prefix for the upstream Qt/AppImage build. Default: $HOME/deps
  --jobs      Maximum parallel build jobs. Default: 4
  --force     Rebuild even if the output AppImage already exists.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      OUTPUT_PATH="$2"
      shift 2
      ;;
    --deps-dir)
      DEPS_DIR="$2"
      shift 2
      ;;
    --jobs)
      JOBS="$2"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --inside-docker)
      INSIDE_DOCKER=1
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

OUTPUT_PATH=$(realpath -m "$OUTPUT_PATH")
DEPS_DIR=$(realpath -m "$DEPS_DIR")

if ! [[ "$JOBS" =~ ^[0-9]+$ ]] || [[ "$JOBS" -lt 1 ]]; then
  echo "--jobs must be a positive integer" >&2
  exit 1
fi

if [[ ! -d "$PCSX2_ROOT" ]]; then
  echo "Vendored PCSX2 source tree is missing: $PCSX2_ROOT" >&2
  exit 1
fi

if [[ -f "$OUTPUT_PATH" && "$FORCE" != "1" ]]; then
  echo "Reusing existing AppImage: $OUTPUT_PATH"
  exit 0
fi

mkdir -p "$(dirname "$OUTPUT_PATH")" "$BUILD_ROOT" "$DEPS_DIR"

GETCONF_SHIM_DIR="$BUILD_ROOT/shims"
mkdir -p "$GETCONF_SHIM_DIR"
cat > "$GETCONF_SHIM_DIR/getconf" <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == "_NPROCESSORS_ONLN" ]]; then
  printf '%s\n' "$JOBS"
  exit 0
fi
exec /usr/bin/getconf "\$@"
EOF
chmod +x "$GETCONF_SHIM_DIR/getconf"

CLANG_C=$(command -v clang-17 || command -v clang || true)
CLANG_CXX=$(command -v clang++-17 || command -v clang++ || true)
CCACHE_BIN=$(command -v ccache || true)

missing_tools=0
for required in cmake ninja git curl strip; do
  if ! command -v "$required" >/dev/null 2>&1; then
    missing_tools=1
  fi
done

if [[ "$INSIDE_DOCKER" != "1" && ( "$missing_tools" == "1" || -z "$CLANG_C" || -z "$CLANG_CXX" ) ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required for the fallback PCSX2 AppImage build environment." >&2
    exit 1
  fi

  deps_parent=$(dirname "$DEPS_DIR")
  mkdir -p "$deps_parent"
  echo "Missing local PCSX2 build prerequisites; building vendored PCSX2 AppImage inside docker."
  docker_force=""
  if [[ "$FORCE" == "1" ]]; then
    docker_force="--force"
  fi
  docker run --rm \
    -e PS2ENV_PCSX2_PATCHES_URL="$PATCHES_URL" \
    -e PS2ENV_PCSX2_JOBS="$JOBS" \
    -e HOST_UID="$(id -u)" \
    -e HOST_GID="$(id -g)" \
    -v "$REPO_ROOT:$REPO_ROOT" \
    -v "$deps_parent:$deps_parent" \
    -w "$REPO_ROOT" \
    ubuntu:22.04 \
    bash -lc "
      set -euo pipefail
      export DEBIAN_FRONTEND=noninteractive
      apt-get update
      apt-get install -y --no-install-recommends ca-certificates wget
      wget -qO /etc/apt/trusted.gpg.d/apt.llvm.org.asc https://apt.llvm.org/llvm-snapshot.gpg.key
      printf '%s\n' 'deb http://apt.llvm.org/jammy/ llvm-toolchain-jammy-17 main' > /etc/apt/sources.list.d/llvm.list
      apt-get update
      apt-get install -y --no-install-recommends \
        build-essential ca-certificates ccache clang-17 cmake curl extra-cmake-modules git libasound2-dev libaio-dev \
        libavcodec-dev libavformat-dev libavutil-dev libcurl4-openssl-dev libdbus-1-dev libdecor-0-dev libegl-dev libevdev-dev libfontconfig-dev libfreetype-dev libfuse2 \
        libgtk-3-dev libgudev-1.0-dev libharfbuzz-dev libinput-dev libopengl-dev libopus-dev libpcap-dev libpipewire-0.3-dev \
        libpulse-dev libssl-dev libswresample-dev libswscale-dev libudev-dev libva-dev libvpl2 libvpl-dev libwayland-dev libx11-dev libx11-xcb-dev libx264-dev \
        libxcb1-dev libxcb-composite0-dev libxcb-cursor-dev libxcb-damage0-dev libxcb-glx0-dev libxcb-icccm4-dev \
        libxcb-image0-dev libxcb-keysyms1-dev libxcb-present-dev libxcb-randr0-dev libxcb-render0-dev libxcb-render-util0-dev \
        libxcb-shape0-dev libxcb-shm0-dev libxcb-sync-dev libxcb-util-dev libxcb-xfixes0-dev libxcb-xinput-dev libxcb-xkb-dev \
        libxext-dev libxkbcommon-x11-dev libxrandr-dev lld-17 nasm ninja-build patchelf pkg-config wget xz-utils zlib1g-dev
      update-ca-certificates
      git config --global --add safe.directory '$REPO_ROOT'
      git config --global --add safe.directory '$PCSX2_ROOT'
      APPIMAGE_EXTRACT_AND_RUN=1 scripts/build-pcsx2-appimage.sh --inside-docker --output '$OUTPUT_PATH' --deps-dir '$DEPS_DIR' --jobs '$JOBS' $docker_force
      chown -R \"\$HOST_UID:\$HOST_GID\" '$(dirname "$OUTPUT_PATH")' '$DEPS_DIR' '$BUILD_ROOT' || true
    "
  exit 0
fi

if [[ -z "$CLANG_C" || -z "$CLANG_CXX" ]]; then
  echo "clang/clang++ are required to build vendored PCSX2." >&2
  exit 1
fi

deps_markers=(
  "$DEPS_DIR/bin/qmake"
  "$DEPS_DIR/lib/cmake/Qt6Core/Qt6CoreConfig.cmake"
  "$DEPS_DIR/lib/cmake/plutovg/plutovgConfig.cmake"
  "$DEPS_DIR/lib/cmake/plutosvg/plutosvgConfig.cmake"
  "$DEPS_DIR/lib/cmake/KDDockWidgets-qt6/KDDockWidgets-qt6Config.cmake"
  "$DEPS_DIR/lib/cmake/ryml/rymlConfig.cmake"
)

deps_ready=1
for marker in "${deps_markers[@]}"; do
  if [[ ! -e "$marker" ]]; then
    deps_ready=0
    break
  fi
done

if [[ "$deps_ready" != "1" ]]; then
  echo "Building PCSX2 Qt dependencies into $DEPS_DIR"
  pushd "$BUILD_ROOT" >/dev/null
  PATH="$GETCONF_SHIM_DIR:$PATH" CMAKE_BUILD_PARALLEL_LEVEL="$JOBS" NINJAFLAGS="-j$JOBS" BUILD_FFMPEG=0 \
    "$PCSX2_ROOT/.github/workflows/scripts/linux/build-dependencies-qt.sh" "$DEPS_DIR"
  popd >/dev/null
fi

rm -f "$DEPS_DIR"/lib/libavcodec* "$DEPS_DIR"/lib/libavformat* "$DEPS_DIR"/lib/libavutil* \
  "$DEPS_DIR"/lib/libswresample* "$DEPS_DIR"/lib/libswscale* "$DEPS_DIR"/lib/pkgconfig/libavcodec.pc \
  "$DEPS_DIR"/lib/pkgconfig/libavformat.pc "$DEPS_DIR"/lib/pkgconfig/libavutil.pc \
  "$DEPS_DIR"/lib/pkgconfig/libswresample.pc "$DEPS_DIR"/lib/pkgconfig/libswscale.pc
rm -rf "$DEPS_DIR"/include/libavcodec "$DEPS_DIR"/include/libavformat "$DEPS_DIR"/include/libavutil \
  "$DEPS_DIR"/include/libswresample "$DEPS_DIR"/include/libswscale

mkdir -p "$PCSX2_ROOT/bin/resources"
if [[ ! -f "$PCSX2_ROOT/bin/resources/patches.zip" ]]; then
  echo "Downloading PCSX2 patches.zip"
  curl -fsSL -o "$PCSX2_ROOT/bin/resources/patches.zip" "$PATCHES_URL/patches.zip"
fi

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

cmake_args=(
  -S "$PCSX2_ROOT"
  -B "$BUILD_DIR"
  -G Ninja
  -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_PREFIX_PATH="$DEPS_DIR"
  -DCMAKE_C_COMPILER="$CLANG_C"
  -DCMAKE_CXX_COMPILER="$CLANG_CXX"
  -DENABLE_SETCAP=OFF
  -DDISABLE_ADVANCE_SIMD=TRUE
  -DUSE_LINKED_FFMPEG=ON
  -DCMAKE_DISABLE_PRECOMPILE_HEADERS=ON
)

if [[ -n "$CCACHE_BIN" ]]; then
  cmake_args+=(
    -DCMAKE_C_COMPILER_LAUNCHER="$CCACHE_BIN"
    -DCMAKE_CXX_COMPILER_LAUNCHER="$CCACHE_BIN"
  )
fi

echo "Configuring vendored PCSX2 build"
cmake "${cmake_args[@]}"

echo "Building vendored PCSX2"
ninja -C "$BUILD_DIR" -j"$JOBS"

pushd "$BUILD_ROOT" >/dev/null
APPIMAGE_EXTRACT_AND_RUN=1 "$PCSX2_ROOT/.github/workflows/scripts/linux/appimage-qt.sh" "$PCSX2_ROOT" "$BUILD_DIR" "$DEPS_DIR" "pcsx2-qt"
popd >/dev/null

mv "$BUILD_ROOT/pcsx2-qt.AppImage" "$OUTPUT_PATH"
chmod +x "$OUTPUT_PATH"
echo "Built PCSX2 AppImage: $OUTPUT_PATH"
