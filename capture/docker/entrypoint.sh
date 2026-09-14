#!/bin/sh
set -eu

PORT="${PORT:-7003}"
HOST="${HOST:-0.0.0.0}"
LIB_PATH="${LIB_PATH:-/app/lib/x86_64/Release/libXCloudSDK.so}"
SDK_CONFIG_PATH="${SDK_CONFIG_PATH:-/app/XCloudSDKTest_config.ini}"
MINIO_CONFIG_PATH="${MINIO_CONFIG_PATH:-/app/minio_config.json}"
PICTURE_DIR="${PICTURE_DIR:-/app/saved_datas/picture}"
USE_XVFB="${USE_XVFB:-1}"
XVFB_DISPLAY="${XVFB_DISPLAY:-:99}"
XVFB_SERVER_ARGS="${XVFB_SERVER_ARGS:--screen 0 1920x1080x24 -ac}"

mkdir -p "${PICTURE_DIR}" /app/log /app/saved_datas /app/lib/x86_64/Release /app/Device

if [ ! -f "${LIB_PATH}" ]; then
  echo "❌ libXCloudSDK.so not found: ${LIB_PATH}" >&2
  echo "Mount it into the container, e.g.:" >&2
  echo "  -v ./lib/x86_64/Release:/app/lib/x86_64/Release:ro" >&2
  exit 2
fi

if [ ! -f "${SDK_CONFIG_PATH}" ]; then
  echo "⚠️  SDK config not found: ${SDK_CONFIG_PATH}" >&2
fi

if [ ! -f "${MINIO_CONFIG_PATH}" ]; then
  echo "⚠️  MinIO config not found: ${MINIO_CONFIG_PATH} (storage=cloud will return error)" >&2
fi

lib_dir="$(dirname "${LIB_PATH}")"
if [ -n "${LD_LIBRARY_PATH:-}" ]; then
  export LD_LIBRARY_PATH="${lib_dir}:${LD_LIBRARY_PATH}"
else
  export LD_LIBRARY_PATH="${lib_dir}"
fi

echo "🔍 Checking shared library deps (ldd)..."
if command -v ldd >/dev/null 2>&1; then
  missing="$(ldd "${LIB_PATH}" 2>/dev/null | awk '/not found/ {print}')"
  if [ -z "${missing}" ]; then
    echo "✅ lib deps ok"
  else
    echo "❌ Missing shared libraries for ${LIB_PATH}:" >&2
    echo "${missing}" >&2
    exit 3
  fi
fi

disable_arg=""
case "${DISABLE_POOL:-}" in
  1|true|yes|TRUE|YES) disable_arg="--disable-pool" ;;
esac

if [ "${USE_XVFB}" = "1" ] || [ "${USE_XVFB}" = "true" ] || [ "${USE_XVFB}" = "yes" ]; then
  if command -v Xvfb >/dev/null 2>&1; then
    export DISPLAY="${XVFB_DISPLAY}"
    disp_num="${DISPLAY#:}"
    sock="/tmp/.X11-unix/X${disp_num}"
    echo "🖥  Starting Xvfb on DISPLAY=${DISPLAY} ..."
    # Some environments can hang on xvfb-run; start Xvfb directly for robustness.
    # -ac disables access control so we don't need xauth/Xauthority.
    Xvfb "${DISPLAY}" ${XVFB_SERVER_ARGS} >/app/log/xvfb.log 2>&1 &
    xvfb_pid="$!"
    i=0
    while [ "${i}" -lt 50 ]; do
      if [ -S "${sock}" ]; then
        break
      fi
      i=$((i + 1))
      sleep 0.1
    done
    if [ ! -S "${sock}" ]; then
      echo "⚠️  Xvfb did not become ready (sock=${sock}). Continue without Xvfb." >&2
      kill "${xvfb_pid}" >/dev/null 2>&1 || true
      unset DISPLAY
    fi
  else
    echo "⚠️  Xvfb not found; continue without virtual display" >&2
  fi
fi

exec python3 -m xcloudsdk_py \
  --host "${HOST}" \
  --http "${PORT}" \
  --lib "${LIB_PATH}" \
  --sdk-config "${SDK_CONFIG_PATH}" \
  --minio-config "${MINIO_CONFIG_PATH}" \
  --picture-dir "${PICTURE_DIR}" \
  ${disable_arg}
