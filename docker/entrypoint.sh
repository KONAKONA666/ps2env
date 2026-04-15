#!/usr/bin/env bash
set -euo pipefail

cleanup() {
  if [[ -n "${PS2ENV_HOST_UID:-}" && -n "${PS2ENV_HOST_GID:-}" ]] && [[ -d /workspace/output ]]; then
    chown -R "${PS2ENV_HOST_UID}:${PS2ENV_HOST_GID}" /workspace/output || true
  fi
}

trap cleanup EXIT

if [[ $# -eq 0 ]]; then
  python3 -m ps2env.smoke_runtime --help
  exit 0
fi

if [[ "${PS2ENV_INSTALL_NVIDIA_DISPLAY_DRIVER:-1}" == "1" ]] && [[ -x /opt/ps2env/install-nvidia-display-driver.sh ]]; then
  /opt/ps2env/install-nvidia-display-driver.sh
fi

python3 -m ps2env.smoke_runtime "$@"
status=$?
exit "$status"
