#!/bin/sh
# Substitute QWENPAW_PORT in supervisord template and start supervisord.
# Default port 8088; override at runtime with -e QWENPAW_PORT=3000.
set -e

BUNDLED_CUSTOM_CHANNELS_DIR="${QWENPAW_BUNDLED_CUSTOM_CHANNELS_DIR:-/opt/qwenpaw/custom_channels}"
CUSTOM_CHANNELS_DIR="${QWENPAW_WORKING_DIR}/custom_channels"

# Seed bundled custom channels into the persistent working volume.
# Existing channel directories are left untouched so user edits survive restarts.
if [ -d "${BUNDLED_CUSTOM_CHANNELS_DIR}" ]; then
  mkdir -p "${CUSTOM_CHANNELS_DIR}"
  for channel_dir in "${BUNDLED_CUSTOM_CHANNELS_DIR}"/*; do
    [ -d "${channel_dir}" ] || continue
    channel_name="$(basename "${channel_dir}")"
    target_dir="${CUSTOM_CHANNELS_DIR}/${channel_name}"
    if [ ! -e "${target_dir}" ]; then
      cp -a "${channel_dir}" "${target_dir}"
      echo "✓ Bundled custom channel installed: ${channel_name}"
    else
      echo "✓ Custom channel already present, skipping: ${channel_name}"
    fi
  done
fi

# Auto-initialize if config.json is missing (bind mount with empty directory).
if [ ! -f "${QWENPAW_WORKING_DIR}/config.json" ]; then
  echo "⚠️  No config.json found in ${QWENPAW_WORKING_DIR}"
  echo "📦 Running initialization..."
  qwenpaw init --defaults --accept-security
  echo "✅ Initialization complete!"
else
  echo "✓ Config found in ${QWENPAW_WORKING_DIR}, skipping initialization."
fi

export QWENPAW_PORT="${QWENPAW_PORT:-8088}"
envsubst '${QWENPAW_PORT}' \
  < /etc/supervisor/conf.d/supervisord.conf.template \
  > /etc/supervisor/conf.d/supervisord.conf
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
