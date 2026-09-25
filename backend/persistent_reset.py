"""Reset of SolarTrigger mutable application data."""

from __future__ import annotations

import shutil
from pathlib import Path

from backend.runtime_paths import ensure_var_layout


_PRESERVED_TOP_LEVEL = frozenset({"tls"})


def _reset_target(var_dir: Path) -> Path:
    """Return the real mutable tree without destroying a deployment symlink."""
    if var_dir.is_symlink():
        target = var_dir.resolve(strict=False)
        if target.exists() and not target.is_dir():
            raise RuntimeError(f"SolarTrigger var symlink target is not a directory: {target}")
        target.mkdir(parents=True, exist_ok=True)
        return target

    if var_dir.exists() and not var_dir.is_dir():
        var_dir.unlink()

    var_dir.mkdir(parents=True, exist_ok=True)
    return var_dir


def reset_application_var(var_dir: Path) -> None:
    """Erase mutable application data while preserving runtime infrastructure.

    ``var`` may be a deployment symlink (for example ``dev-active/var`` pointing
    at the shared persistent tree).  The symlink itself is part of the deployment
    layout and must never be replaced.  TLS material is also preserved because
    nginx requires it to restart after the reset/reboot operation.
    """
    var_dir = Path(var_dir)
    target = _reset_target(var_dir)

    for child in tuple(target.iterdir()):
        if child.name in _PRESERVED_TOP_LEVEL:
            continue
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            shutil.rmtree(child)

    ensure_var_layout(target)
