#!/usr/bin/env bash
set -euo pipefail

# One-time migration for an already installed SolarTrigger Pi.
# This script changes systemd ownership only; it does not copy application code.

APP_DIR="${1:-/home/airone/solar-eclipse-trigger-prod}"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: run as root (sudo)." >&2
    exit 1
fi

if [[ ! -d "$APP_DIR" ]]; then
    echo "ERROR: application directory not found: $APP_DIR" >&2
    exit 1
fi

if [[ ! -f "$APP_DIR/backend/runtime_daemon.py" ]]; then
    echo "ERROR: standalone runtime code is not deployed in $APP_DIR." >&2
    exit 1
fi

if [[ ! -f "$APP_DIR/backend/indi_server_daemon.py" ]] || \
   [[ ! -f "$APP_DIR/configs/indi_default.json" ]]; then
    echo "ERROR: central INDI manager code/config is not deployed in $APP_DIR." >&2
    exit 1
fi

if pgrep -f "$APP_DIR/scripts/eclipse_trigger.py" >/dev/null 2>&1; then
    echo "ERROR: an eclipse trigger is currently active; runtime migration is forbidden." >&2
    exit 1
fi

VENV_DIR="$APP_DIR/venv"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "ERROR: Python venv not found: $VENV_DIR" >&2
    exit 1
fi

CURRENT_USER="$(stat -c '%U' "$APP_DIR")"
CURRENT_GROUP="$(id -gn "$CURRENT_USER")"

CAMLIBS_DIR=$(find /usr/local/lib/libgphoto2 \
    -maxdepth 1 -mindepth 1 -type d 2>/dev/null \
    | sort -V | tail -1 || true)
IOLIBS_DIR=$(find /usr/local/lib/libgphoto2_port \
    -maxdepth 1 -mindepth 1 -type d 2>/dev/null \
    | sort -V | tail -1 || true)

CAMLIBS_ENV=""
IOLIBS_ENV=""
[[ -n "$CAMLIBS_DIR" ]] && CAMLIBS_ENV="Environment=\"CAMLIBS=$CAMLIBS_DIR\""
[[ -n "$IOLIBS_DIR" ]] && IOLIBS_ENV="Environment=\"IOLIBS=$IOLIBS_DIR\""

cat > /etc/systemd/system/solartrigger-indi.service <<EOF
[Unit]
Description=SolarTrigger INDI astronomical equipment server
After=network.target local-fs.target

[Service]
Type=simple
User=$CURRENT_USER
Group=$CURRENT_GROUP
WorkingDirectory=$APP_DIR
Environment="PATH=$VENV_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONPATH=$APP_DIR"
ExecStart=$VENV_DIR/bin/python -m backend.indi_server_daemon \
    --config $APP_DIR/configs/indi_default.json
Restart=on-failure
RestartSec=2
StandardOutput=journal
StandardError=journal
SyslogIdentifier=solartrigger-indi

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/solartrigger-runtime.service <<EOF
[Unit]
Description=SolarTrigger Autonomous Runtime
After=network.target local-fs.target solartrigger-indi.service
Wants=network.target solartrigger-indi.service

[Service]
Type=simple
User=$CURRENT_USER
Group=$CURRENT_GROUP
WorkingDirectory=$APP_DIR
RuntimeDirectory=solartrigger
RuntimeDirectoryMode=0770
Environment="PATH=$VENV_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONPATH=$APP_DIR"
Environment="LD_LIBRARY_PATH=/usr/local/lib"
Environment="SOLARTRIGGER_ROOT=$APP_DIR"
Environment="SOLARTRIGGER_RUNTIME_SOCKET=/run/solartrigger/runtime.sock"
$CAMLIBS_ENV
$IOLIBS_ENV
ExecStart=$VENV_DIR/bin/python -m backend.runtime_daemon \
    --root $APP_DIR \
    --socket /run/solartrigger/runtime.sock

Restart=on-failure
RestartSec=2
TimeoutStopSec=45
KillMode=control-group
StandardOutput=journal
StandardError=journal
SyslogIdentifier=solartrigger-runtime

[Install]
WantedBy=multi-user.target
EOF

# Existing installations may still have a legacy WSGI entrypoint that only
# imports the Flask app. The autonomous runtime relay depends on the portal
# background threads, so keep the production WSGI entrypoint canonical.
cat > "$APP_DIR/wsgi.py" <<'EOF'
from app import app, socketio, start_background_threads

start_background_threads()

if __name__ == "__main__":
    socketio.run(app)
EOF
chown "$CURRENT_USER:$CURRENT_GROUP" "$APP_DIR/wsgi.py"
chmod 644 "$APP_DIR/wsgi.py"

mkdir -p /etc/systemd/system/solareclipse.service.d
cat > /etc/systemd/system/solareclipse.service.d/standalone-runtime.conf <<'EOF'
[Unit]
Requires=solartrigger-runtime.service
Wants=solartrigger-indi.service
After=solartrigger-indi.service solartrigger-runtime.service

[Service]
Environment="SOLARTRIGGER_RUNTIME_CLIENT=1"
Environment="SOLARTRIGGER_RUNTIME_SOCKET=/run/solartrigger/runtime.sock"
Environment="SOLARTRIGGER_ADMISSION_LOCK=/run/solartrigger/admission.lock"
EOF

# Retire l'ancien serveur mono-EQMod s'il existe encore.
systemctl disable --now indiserver-eqmod.service 2>/dev/null || true
rm -f /etc/systemd/system/indiserver-eqmod.service

systemctl daemon-reload
systemctl enable solartrigger-indi.service
systemctl enable solartrigger-runtime.service

if ! systemctl restart solartrigger-indi.service; then
    echo "ERROR: solartrigger-indi.service failed to start." >&2
    systemctl --no-pager --full status solartrigger-indi.service >&2 || true
    exit 1
fi

if ! systemctl restart solartrigger-runtime.service; then
    echo "ERROR: solartrigger-runtime.service failed to start." >&2
    systemctl --no-pager --full status solartrigger-runtime.service >&2 || true
    exit 1
fi

if ! systemctl restart solareclipse.service; then
    echo "ERROR: solareclipse.service failed to restart with runtime client mode." >&2
    systemctl --no-pager --full status solareclipse.service >&2 || true
    exit 1
fi

systemctl is-active --quiet solartrigger-indi.service
systemctl is-active --quiet solartrigger-runtime.service
systemctl is-active --quiet solareclipse.service

echo "Standalone runtime + INDI migration complete."
echo "INDI   : active"
echo "Runtime: active"
echo "Portal : active"
