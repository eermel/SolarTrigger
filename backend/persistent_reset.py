"""Reset of SolarTrigger mutable application data."""

from __future__ import annotations

import shutil
from pathlib import Path

from backend.runtime_paths import ensure_var_layout


_PRESERVED_TOP_LEVEL = frozenset({"tls"})
_PRESERVED_GENERATED_CHILDREN = frozenset({
    "camera_profiles",
    "camera_timing",
    "camera_characterization",
})


def _reset_target(var_dir: Path) -> Path:
    """Return the real mutable tree without destroying a deployment symlink."""
    if var_dir.is_symlink():
        target = var_dir.resolve(strict=False)
        if target.exists() and not target.is_dir():
            raise RuntimeError(
                f"SolarTrigger var symlink target is not a directory: {target}"
            )
        target.mkdir(parents=True, exist_ok=True)
        return target

    if var_dir.exists() and not var_dir.is_dir():
        var_dir.unlink()

    var_dir.mkdir(parents=True, exist_ok=True)
    return var_dir


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _reset_generated(generated_dir: Path) -> None:
    """Erase generated runtime/user data except durable camera qualification."""
    if generated_dir.is_symlink() or (
        generated_dir.exists() and not generated_dir.is_dir()
    ):
        _remove_path(generated_dir)

    generated_dir.mkdir(parents=True, exist_ok=True)
    for child in tuple(generated_dir.iterdir()):
        if child.name in _PRESERVED_GENERATED_CHILDREN:
            continue
        _remove_path(child)


def reset_application_var(var_dir: Path) -> None:
    """Erase mutable application data while preserving durable infrastructure.

    var may be a deployment symlink (for example dev-active/var pointing at the
    shared persistent tree). The symlink itself is part of the deployment layout
    and must never be replaced.

    TLS material is preserved because nginx requires it after reboot. Camera
    characterization/qualification data is also durable: characterized profiles,
    timing contracts, characterization history and validation reports survive
    this reset.
    """
    var_dir = Path(var_dir)
    target = _reset_target(var_dir)

    for child in tuple(target.iterdir()):
        if child.name in _PRESERVED_TOP_LEVEL:
            continue
        if child.name == "generated":
            _reset_generated(child)
            continue
        _remove_path(child)

    ensure_var_layout(target)
