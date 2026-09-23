#!/bin/bash
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "Run this bootstrap with sudo." >&2
    exit 1
fi

APP_USER="${SUDO_USER:-airone}"
USER_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
if [ -z "$USER_HOME" ] || [ ! -d "$USER_HOME" ]; then
    echo "Unable to resolve home directory for $APP_USER" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for HELPER in solartrigger-system-update solartrigger-release-update; do
    SOURCE="$SCRIPT_DIR/$HELPER"
    if [ ! -f "$SOURCE" ]; then
        echo "Missing helper: $SOURCE" >&2
        exit 1
    fi
    install -o root -g root -m 0755 "$SOURCE" "/usr/local/sbin/$HELPER"
done

SUDOERS_FILE="/etc/sudoers.d/solareclipse-maintenance"
cat > "$SUDOERS_FILE" <<EOF
$APP_USER ALL=(root) NOPASSWD: /usr/local/sbin/solartrigger-system-update
$APP_USER ALL=(root) NOPASSWD: /usr/local/sbin/solartrigger-release-update *
EOF
chmod 0440 "$SUDOERS_FILE"

if command -v visudo >/dev/null 2>&1; then
    visudo -cf "$SUDOERS_FILE" >/dev/null
fi

# Nginx defaults to a 1 MiB request body, which is too small for an offline
# SolarTrigger release ZIP. Keep transport headroom above the application
# validator's 256 MiB package limit.
if command -v nginx >/dev/null 2>&1; then
    NGINX_UPLOAD_CONF="/etc/nginx/conf.d/solartrigger-upload.conf"
    install -d -o root -g root -m 0755 /etc/nginx/conf.d
    cat > "$NGINX_UPLOAD_CONF" <<'EOF'
client_max_body_size 300m;
EOF
    chmod 0644 "$NGINX_UPLOAD_CONF"
    nginx -t
    systemctl reload nginx
fi

echo "SolarTrigger web-update bootstrap installed for $APP_USER."
echo "Web upload limit configured for SolarTrigger release packages."
echo "No release was switched. The first web update will migrate the legacy layout safely."
