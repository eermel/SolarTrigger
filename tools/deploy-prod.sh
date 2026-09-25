#!/usr/bin/env bash
set -euo pipefail

SRC="${SOLARTRIGGER_DEPLOY_SRC:-/home/airone/dev/solar-eclipse-trigger}"
DST_HOST="${SOLARTRIGGER_DEPLOY_HOST:-airone@trigger1}"
ACTIVE_DST="${SOLARTRIGGER_DEPLOY_ACTIVE:-/home/airone/solar-eclipse-trigger-prod}"
DEV_DST="${SOLARTRIGGER_DEPLOY_DEV:-/home/airone/solartrigger/dev-active}"
REMOTE_HELPER="${SOLARTRIGGER_DEPLOY_HELPER:-/usr/local/sbin/solartrigger-release-update}"
CAMERA_SHARED_BASE="${SOLARTRIGGER_DEPLOY_CAMERA_SHARED:-/home/airone/solartrigger/var/generated}"
DST="$DEV_DST"

DRY_RUN=0

if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
elif [[ $# -gt 0 ]]; then
    echo "Usage: $0 [--dry-run]" >&2
    exit 2
fi

remote_helper_supports_dev_prepare() {
    ssh "$DST_HOST"         "grep -q '^[[:space:]]*dev-prepare)' '$REMOTE_HELPER' 2>/dev/null"
}

bootstrap_remote_helper() {
    local remote_tmp="/tmp/solartrigger-release-update.$$"

    if remote_helper_supports_dev_prepare; then
        return 0
    fi

    echo "Remote release helper is older than the DEV workspace workflow."
    echo "Installing the current helper once; sudo may request the Pi password."

    scp "$SRC/install/solartrigger-release-update"         "$DST_HOST:$remote_tmp"

    ssh -t "$DST_HOST"         "sudo install -o root -g root -m 0755 '$remote_tmp' '$REMOTE_HELPER' && rm -f '$remote_tmp'"

    if ! remote_helper_supports_dev_prepare; then
        echo "ERROR: remote helper upgrade did not expose dev-prepare." >&2
        exit 1
    fi
}

prepare_remote_dev_workspace() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "Would run: sudo -n $REMOTE_HELPER dev-prepare"
        if ! ssh "$DST_HOST" "test -d '$DEV_DST'"; then
            echo "DEV workspace does not exist yet; dry-run stops before rsync."
            exit 0
        fi
        return 0
    fi

    bootstrap_remote_helper

    ssh "$DST_HOST"         "sudo -n '$REMOTE_HELPER' dev-prepare"

    local active_target
    active_target="$(ssh "$DST_HOST" "readlink -f '$ACTIVE_DST' 2>/dev/null || true")"

    if [[ "$active_target" != "$DEV_DST" ]]; then
        echo "ERROR: DEV workspace was not activated:" >&2
        echo "  $ACTIVE_DST -> ${active_target:-<missing>}" >&2
        echo "  expected     $DEV_DST" >&2
        exit 1
    fi
}

ensure_camera_persistent_links() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "Would verify persistent camera links under $DST/configs/"
        return 0
    fi

    ssh "$DST_HOST" "
        set -e
        mkdir -p '$DST/configs'
        for name in camera_characterization camera_profiles camera_timing; do
            target='$DST/configs/'\$name
            shared='$CAMERA_SHARED_BASE/'\$name

            if [ ! -d \"\$shared\" ]; then
                echo \"ERROR: persistent camera directory missing: \$shared\" >&2
                exit 1
            fi
            if [ -e \"\$target\" ] && [ ! -L \"\$target\" ]; then
                echo \"ERROR: refusing to replace non-symlink camera path: \$target\" >&2
                exit 1
            fi

            ln -sfn \"\$shared\" \"\$target\"

            if [ \"\$(readlink -f \"\$target\")\" != \"\$(readlink -f \"\$shared\")\" ]; then
                echo \"ERROR: persistent camera link verification failed: \$target\" >&2
                exit 1
            fi
        done
    "
}

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
    "$SRC/flask_app/wsgi.py"
    "$SRC/flask_app/templates/index.html"
    "$SRC/flask_app/static/js"
    "$SRC/flask_app/static/css"
    "$SRC/Sounds"
    "$SRC/configs"
    "$SRC/install/install_standalone_runtime_service.sh"
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

if [[ ! -f "$SRC/install/solartrigger-release-update" ]]; then
    echo "ERROR: required release helper missing: $SRC/install/solartrigger-release-update" >&2
    exit 1
fi

prepare_remote_dev_workspace

echo "=== Solar Eclipse Trigger DEV deploy ==="
echo "SRC    : $SRC"
echo "ACTIVE : $DST_HOST:$ACTIVE_DST"
echo "DEV    : $DST_HOST:$DST"

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "MODE: DRY RUN"
else
    echo "MODE: REAL DEPLOY"
fi

echo

echo "=== backend ==="
rsync "${RSYNC_OPTS[@]}" --delete \
    "$SRC/backend/" \
    "$DST_HOST:$DST/backend/"

echo
echo "=== services ==="
rsync "${RSYNC_OPTS[@]}" --delete \
    "$SRC/services/" \
    "$DST_HOST:$DST/services/"

echo
echo "=== plugins ==="
rsync "${RSYNC_OPTS[@]}" --delete \
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
echo "=== wsgi.py : production entrypoint ==="
rsync "${RSYNC_OPTS[@]}" \
    "$SRC/flask_app/wsgi.py" \
    "$DST_HOST:$DST/wsgi.py"

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
    --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
    "$SRC/flask_app/static/js/" \
    "$DST_HOST:$DST/static/js/"

echo
echo "=== frontend CSS ==="
rsync "${RSYNC_OPTS[@]}" --delete \
    --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
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
    --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
    "$SRC/Sounds/" \
    "$DST_HOST:$DST/static/sounds/"

echo
echo "=== product configs ==="
# Les configs produit sont synchronisées exactement, sauf les données
# issues de la caractérisation caméra, qui sont persistantes et locales à la Pi.
rsync "${RSYNC_OPTS[@]}" --delete \
    --exclude='camera_characterization' \
    --exclude='camera_profiles' \
    --exclude='camera_timing' \
    "$SRC/configs/" \
    "$DST_HOST:$DST/configs/"

ensure_camera_persistent_links

echo
echo "=== system migration script (copied, never executed automatically) ==="
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Would copy install/install_standalone_runtime_service.sh"
else
    ssh "$DST_HOST" "mkdir -p '$DST/install'"
fi
rsync "${RSYNC_OPTS[@]}" \
    "$SRC/install/install_standalone_runtime_service.sh" \
    "$DST_HOST:$DST/install/install_standalone_runtime_service.sh"

echo
echo "=== preserved camera characterization data ==="
echo "  $DST/configs/camera_characterization/"
echo "  $DST/configs/camera_profiles/"
echo "  $DST/configs/camera_timing/"

echo
echo "=== build metadata ==="

BUILD_COMMIT="$(git -C "$SRC" rev-parse HEAD)"

# Un déploiement de validation peut contenir des changements non commités.
# BUILD_COMMIT doit alors l'indiquer explicitement pour éviter de prétendre
# que le code PROD correspond exactement au commit Git.
if [[ -n "$(git -C "$SRC" status --porcelain)" ]]; then
    BUILD_COMMIT="${BUILD_COMMIT}-dirty"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Would write BUILD_COMMIT=$BUILD_COMMIT"
    echo "Would remove legacy $DST/VERSION"
else
    printf '%s\n' "$BUILD_COMMIT" | \
        ssh "$DST_HOST" \
        "cat > '$DST/BUILD_COMMIT' && rm -f '$DST/VERSION' '$DST/RELEASE_VERSION' '$DST/RELEASE_MANIFEST.json'"
fi

echo
echo "=== DEV deploy complete ==="
echo
echo "NEVER DEPLOYED BY THIS SCRIPT:"
echo "  var/   (all persistent/generated/runtime application data)"
echo "  venv"
echo
echo "--delete is used only inside disposable DEV code/config/static trees; var/ remains untouched."
echo "var/ is never synchronized or deleted."
echo "Service is NOT restarted automatically."
echo "INDI/systemd migration is NOT executed automatically."
echo "After an explicit operator decision, run on the Pi:"
echo "  sudo $ACTIVE_DST/install/install_standalone_runtime_service.sh $ACTIVE_DST"
