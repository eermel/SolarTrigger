"""Shared exception types for camera characterization."""
from __future__ import annotations


class Cancelled(RuntimeError):
    """Operator or supervisor requested characterization cancellation."""

