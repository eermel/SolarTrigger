#!/usr/bin/env bash
set -euo pipefail

SRC="/home/airone/dev/solar-eclipse-trigger"
DST_HOST="airone@trigger1"
DST="/home/airone/solar-eclipse-trigger-prod"

DRY_RUN=0

if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
elif [[ $# -gt 0 ]]; then
    echo "Usage: $0 [--dry-run]" >&2
    exit 2
fi

RSYNC_OPTS=(
    -av
    --exclude='__pycache__/'
    --exclude='*.pyc'
    --exclude='*.pyo'
    --exclude='.pytest_cache/'
)

if [[ "$DRY_RUN" -eq 1 ]]; then
    RSYNC_OPTS+=(-n)
fi

required=(
    "$SRC/backend"
    "$SRC/services"
    "$SRC/plugins"
    "$SRC/scripts"
    "$SRC/flask_app/app.py"
    "$SRC/flask_app/templates/index.html"
    "$SRC/Sounds"
    "$SRC/configs/camera_timing"
)

for path in "${required[@]}"; do
    if [[ ! -e "$path" ]]; then
        echo "ERROR: required source missing: $path" >&2
        exit 1
    fi
done

echo "=== Solar Eclipse Trigger PROD deploy ==="
echo "SRC : $SRC"
echo "DST : $DST_HOST:$DST"

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "MODE: DRY RUN"
else
    echo "MODE: REAL DEPLOY"
fi

echo

echo "=== backend ==="
rsync "${RSYNC_OPTS[@]}" \
    "$SRC/backend/" \
    "$DST_HOST:$DST/backend/"

echo
echo "=== services ==="
rsync "${RSYNC_OPTS[@]}" \
    "$SRC/services/" \
    "$DST_HOST:$DST/services/"

echo
echo "=== plugins ==="
rsync "${RSYNC_OPTS[@]}" \
    "$SRC/plugins/" \
    "$DST_HOST:$DST/plugins/"

echo
echo "=== runtime scripts ==="
rsync "${RSYNC_OPTS[@]}" \
    --include='/__init__.py' \
    --include='/camera_ipc_client.py' \
    --include='/eclipse_calculator_py.py' \
    --include='/eclipse_trigger.py' \
    --include='/fanout_camera_adapter.py' \
    --include='/gps_sync.py' \
    --exclude='/*' \
    "$SRC/scripts/" \
    "$DST_HOST:$DST/scripts/"

echo
echo "=== app.py : VM layout -> PROD layout ==="
rsync "${RSYNC_OPTS[@]}" \
    "$SRC/flask_app/app.py" \
    "$DST_HOST:$DST/app.py"

echo
echo "=== index.html : VM layout -> PROD layout ==="
rsync "${RSYNC_OPTS[@]}" \
    "$SRC/flask_app/templates/index.html" \
    "$DST_HOST:$DST/templates/index.html"

echo
echo "=== sound directories ==="
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Would ensure:"
    echo "  $DST/Sounds"
    echo "  $DST/static/sounds"
else
    ssh "$DST_HOST" \
        "mkdir -p '$DST/Sounds' '$DST/static/sounds'"
fi

echo
echo "=== runtime sounds ==="
rsync "${RSYNC_OPTS[@]}" \
    "$SRC/Sounds/" \
    "$DST_HOST:$DST/Sounds/"

echo
echo "=== web sounds ==="
rsync "${RSYNC_OPTS[@]}" \
    "$SRC/Sounds/" \
    "$DST_HOST:$DST/static/sounds/"

echo
echo "=== camera timing profiles ==="
rsync "${RSYNC_OPTS[@]}" \
    "$SRC/configs/camera_timing/" \
    "$DST_HOST:$DST/configs/camera_timing/"

echo
echo "=== build metadata ==="

BUILD_COMMIT="$(git -C "$SRC" rev-parse HEAD)"

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Would write BUILD_COMMIT=$BUILD_COMMIT"
    echo "Would remove legacy $DST/VERSION"
else
    printf '%s\n' "$BUILD_COMMIT" | \
        ssh "$DST_HOST" \
        "cat > '$DST/BUILD_COMMIT' && rm -f '$DST/VERSION'"
fi

echo
echo "=== Complete ==="
echo
echo "NEVER DEPLOYED BY THIS SCRIPT:"
echo "  state.json"
echo "  configs/rig/default.json"
echo "  configs/execution_plan/"
echo "  configs/circumstances/"
echo "  configs/photo_cfg/"
echo "  logs / runtime data"
echo "  venv"
echo
echo "No --delete is used."
echo "Service is NOT restarted automatically."
