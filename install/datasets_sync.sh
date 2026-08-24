#!/bin/bash

# Copy the eclipse registry and every dataset it references into a trigger tree.
sync_eclipse_datasets() {
    if [ "$#" -ne 2 ]; then
        echo "datasets sync: invalid arguments: expected PACKAGE_DIR and TRIGGER_DIR" >&2
        return 1
    fi

    local package_dir="$1"
    local trigger_dir="$2"
    local source_dir="$package_dir/data/eclipses"
    local registry="$source_dir/registry.json"
    local destination="$trigger_dir/data/eclipses"
    local file_list
    local dataset
    local copied=0

    if [ ! -f "$registry" ]; then
        echo "datasets sync: missing registry: $registry" >&2
        return 1
    fi

    if ! mkdir -p "$destination"; then
        echo "datasets sync: cannot create destination: $destination" >&2
        return 1
    fi
    if ! cp "$registry" "$destination/registry.json"; then
        echo "datasets sync: cannot copy registry: $registry" >&2
        return 1
    fi

    file_list="$(mktemp)" || {
        echo "datasets sync: cannot create temporary file" >&2
        return 1
    }

    if command -v jq >/dev/null 2>&1; then
        if ! jq empty "$registry" >/dev/null 2>&1; then
            echo "datasets sync: invalid JSON in registry: $registry" >&2
            rm -f "$file_list"
            return 1
        fi
        if ! jq -e '
            (.eclipses | type == "array") and
            all(.eclipses[];
                (type == "object") and
                (.file | type == "string") and
                (.file | length > 0) and
                (.file != ".") and (.file != "..") and
                (.file | contains("/") | not) and
                (.file | contains("\\") | not)
            )
        ' "$registry" >/dev/null 2>&1; then
            echo "datasets sync: invalid entry in registry: each eclipse must have a basename file" >&2
            rm -f "$file_list"
            return 1
        fi
        jq -r '.eclipses[].file' "$registry" >"$file_list"
    else
        local python_bin
        if [ -x /usr/bin/python3 ]; then
            python_bin=/usr/bin/python3
        elif command -v python3 >/dev/null 2>&1; then
            python_bin="$(command -v python3)"
        else
            echo "datasets sync: cannot validate registry: jq and python3 are unavailable" >&2
            rm -f "$file_list"
            return 1
        fi

        if ! "$python_bin" -c '
import json
import sys

registry, output = sys.argv[1:]
try:
    with open(registry, encoding="utf-8") as stream:
        document = json.load(stream)
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    print(f"datasets sync: invalid JSON in registry: {registry}: {exc}", file=sys.stderr)
    raise SystemExit(2)

entries = document.get("eclipses") if isinstance(document, dict) else None
if not isinstance(entries, list):
    print("datasets sync: invalid entry in registry: eclipses must be an array", file=sys.stderr)
    raise SystemExit(3)

files = []
for entry in entries:
    name = entry.get("file") if isinstance(entry, dict) else None
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name:
        print("datasets sync: invalid entry in registry: each eclipse must have a basename file", file=sys.stderr)
        raise SystemExit(3)
    files.append(name)

with open(output, "w", encoding="utf-8") as stream:
    for name in files:
        print(name, file=stream)
' "$registry" "$file_list"; then
            rm -f "$file_list"
            return 1
        fi
    fi

    while IFS= read -r dataset; do
        if [ ! -f "$source_dir/$dataset" ]; then
            echo "datasets sync: missing dataset file: $source_dir/$dataset" >&2
            rm -f "$file_list"
            return 1
        fi
        if ! cp "$source_dir/$dataset" "$destination/$dataset"; then
            echo "datasets sync: failed to copy dataset file: $source_dir/$dataset" >&2
            rm -f "$file_list"
            return 1
        fi
        if [ -f "$destination/$dataset" ]; then
            copied=$((copied + 1))
        fi
    done <"$file_list"
    rm -f "$file_list"

    if [ "$copied" -lt 1 ]; then
        echo "datasets sync: no referenced dataset exists at destination: $destination" >&2
        return 1
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    package_dir="${SOLARECLIPSE_TEST_PACKAGE_DIR:-$(dirname "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")}" 
    trigger_dir="${SOLARECLIPSE_TEST_TRIGGER_DIR:?must be set in CLI mode}"
    sync_eclipse_datasets "$package_dir" "$trigger_dir"
fi
