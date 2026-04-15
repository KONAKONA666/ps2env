#!/usr/bin/env bash
set -euo pipefail

HAS_64BIT=false
HAS_32BIT=false

if ldconfig -p 2>/dev/null | grep -q libGLX_nvidia.so.0; then
    HAS_64BIT=true
fi
if ldconfig -p 2>/dev/null | grep -q "libGLX_nvidia.*i386"; then
    HAS_32BIT=true
fi

if $HAS_64BIT && $HAS_32BIT; then
    echo "NVIDIA display driver already installed (64-bit + 32-bit libs found)"
    exit 0
fi

COMPAT32_ONLY=false
if $HAS_64BIT && ! $HAS_32BIT; then
    echo "NVIDIA 64-bit libs found but 32-bit compat libs are missing"
    COMPAT32_ONLY=true
fi

DRIVER_VERSION="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | tr -d '[:space:]')"
if [[ -z "$DRIVER_VERSION" && -f /proc/driver/nvidia/version ]]; then
    DRIVER_VERSION="$(head -n1 /proc/driver/nvidia/version | grep -oP '\d+\.\d+\.\d+' | head -1)"
fi

if [[ -z "$DRIVER_VERSION" ]]; then
    echo "ERROR: could not detect NVIDIA driver version from nvidia-smi or /proc/driver/nvidia/version" >&2
    exit 1
fi

ARCH="$(dpkg --print-architecture | sed -e 's/amd64/x86_64/' -e 's/arm64/aarch64/' -e 's/i.*86/x86/' -e 's/unknown/x86_64/')"
INSTALLER="NVIDIA-Linux-${ARCH}-${DRIVER_VERSION}.run"
XFREE_URL="https://international.download.nvidia.com/XFree86/Linux-${ARCH}/${DRIVER_VERSION}/${INSTALLER}"
TESLA_URL="https://international.download.nvidia.com/tesla/${DRIVER_VERSION}/${INSTALLER}"

echo "Detected host NVIDIA driver ${DRIVER_VERSION} (${ARCH})"

cd /tmp
CACHE_DIR="${NVIDIA_DRIVER_CACHE:-${PS2ENV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/ps2env}}"
CACHED_INSTALLER="$CACHE_DIR/$INSTALLER"
mkdir -p "$CACHE_DIR" || true

if [[ -f "$CACHED_INSTALLER" ]]; then
    cp "$CACHED_INSTALLER" "/tmp/$INSTALLER"
else
    if ! curl -fsSL -o "/tmp/$INSTALLER" "$XFREE_URL"; then
        curl -fsSL -o "/tmp/$INSTALLER" "$TESLA_URL"
    fi
    PARTIAL_CACHE="$CACHED_INSTALLER.part"
    rm -f "$PARTIAL_CACHE"
    if cp "/tmp/$INSTALLER" "$PARTIAL_CACHE"; then
        mv "$PARTIAL_CACHE" "$CACHED_INSTALLER"
    else
        echo "WARNING: failed to populate NVIDIA installer cache at $CACHED_INSTALLER" >&2
        rm -f "$PARTIAL_CACHE"
    fi
fi

sh "/tmp/$INSTALLER" -x
cd "/tmp/NVIDIA-Linux-${ARCH}-${DRIVER_VERSION}"

if $COMPAT32_ONLY; then
    if [[ -d 32 ]]; then
        mkdir -p /usr/lib/i386-linux-gnu
        cp 32/*.so* /usr/lib/i386-linux-gnu/ 2>/dev/null || true
    fi
else
    ./nvidia-installer --silent \
        --no-kernel-module \
        --install-compat32-libs \
        --no-nouveau-check \
        --no-nvidia-modprobe \
        --no-rpms \
        --no-backup \
        --no-check-for-alternate-installs \
        --no-distro-scripts \
        --no-wine-files \
        --no-kernel-module-source
fi

cd /
rm -rf /tmp/NVIDIA-Linux-* "/tmp/$INSTALLER"
ldconfig

echo "NVIDIA display driver ${DRIVER_VERSION} installed"
