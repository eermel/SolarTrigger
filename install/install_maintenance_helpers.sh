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

echo "SolarTrigger web-update bootstrap installed for $APP_USER."
echo "No release was switched. The first web update will migrate the legacy layout safely."
