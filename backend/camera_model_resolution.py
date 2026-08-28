"""Resolve logical camera identities against the loaded sensor database."""

from __future__ import annotations

from backend.sensor_db import lookup_model


def resolve_sensor_entry(manufacturer: str, model_or_alias: str, db: dict) -> dict:
    """Return a normalized copy of the matching sensor database entry.

    ``KeyError`` from :func:`lookup_model` is intentionally propagated when the
    manufacturer and model (or alias) cannot be resolved.
    """

    return lookup_model(db, manufacturer, model_or_alias)
