"""Reset of SolarTrigger mutable application data."""

from __future__ import annotations

import shutil
from pathlib import Path

from backend.runtime_paths import ensure_var_layout


def reset_application_var(var_dir: Path) -> None:
    """Erase the complete mutable application tree and recreate it empty."""
    var_dir = Path(var_dir)

    if var_dir.is_symlink() or var_dir.is_file():
        var_dir.unlink()
    elif var_dir.exists():
        shutil.rmtree(var_dir)

    ensure_var_layout(var_dir)
