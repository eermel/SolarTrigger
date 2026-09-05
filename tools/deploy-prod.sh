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

RUNTIME_SCRIPTS=(
    "__init__.py"
    "camera_ipc_client.py"
    "eclipse_calculator_py.py"
    "eclipse_trigger.py"
    "fanout_camera_adapter.py"
    "gps_sync.py"
)

required=(
    "$SRC/backend"
    "$SRC/services"
    "$SRC/plugins"
    "$SRC/scripts"
    "$SRC/flask_app/app.py"
    "$SRC/flask_app/templates/index.html"
    "$SRC/flask_app/static/js"
    "$SRC/flask_app/static/css"
    "$SRC/Sounds"
    "$SRC/configs"
)

for path in "${required[@]}"; do
    if [[ ! -e "$path" ]]; then
        echo "ERROR: required source missing: $path" >&2
        exit 1
    fi
done

for script in "${RUNTIME_SCRIPTS[@]}"; do
    src="$SRC/scripts/$script"

    if [[ ! -f "$src" ]]; then
        echo "ERROR: required runtime script missing: $src" >&2
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

RUNTIME_SCRIPT_SOURCES=()

for script in "${RUNTIME_SCRIPTS[@]}"; do
    RUNTIME_SCRIPT_SOURCES+=("$SRC/scripts/$script")
done

# scripts/ appartient entièrement au runtime : aucune relique DEV ne doit survivre.
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Would replace $DST/scripts with exactly ${#RUNTIME_SCRIPTS[@]} runtime scripts"
else
    ssh "$DST_HOST" \
        "rm -rf '$DST/scripts' && mkdir -p '$DST/scripts'"
fi

rsync "${RSYNC_OPTS[@]}" \
    "${RUNTIME_SCRIPT_SOURCES[@]}" \
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
echo "=== static and sound directories ==="
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Would ensure:"
    echo "  $DST/Sounds"
    echo "  $DST/static/sounds"
    echo "  $DST/static/js"
    echo "  $DST/static/css"
else
    ssh "$DST_HOST" \
        "mkdir -p '$DST/Sounds' '$DST/static/sounds' '$DST/static/js' '$DST/static/css'"
fi

echo
echo "=== frontend JavaScript ==="
rsync "${RSYNC_OPTS[@]}" --delete \
    "$SRC/flask_app/static/js/" \
    "$DST_HOST:$DST/static/js/"

echo
echo "=== frontend CSS ==="
rsync "${RSYNC_OPTS[@]}" --delete \
    "$SRC/flask_app/static/css/" \
    "$DST_HOST:$DST/static/css/"

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
echo "=== product configs ==="
# configs/ est désormais 100 % produit et peut être synchronisé exactement.
# Cela supprime aussi les anciens fichiers runtime qui vivaient autrefois ici.
rsync "${RSYNC_OPTS[@]}" --delete \
    "$SRC/configs/" \
    "$DST_HOST:$DST/configs/"

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
echo "  var/   (all persistent/generated/runtime application data)"
echo "  venv"
echo
echo "--delete is used ONLY for product configs/ and frontend static assets (js/css)."
echo "var/ is never synchronized or deleted."
echo "Service is NOT restarted automatically."
