"""Read-only access to local eclipse-engine datasets."""

from .loader import (
    EclipseDataError,
    EclipseDataFormatError,
    EclipseNotFoundError,
    list_supported_eclipses,
    load_eclipse,
)

__all__ = [
    "EclipseDataError",
    "EclipseDataFormatError",
    "EclipseNotFoundError",
    "list_supported_eclipses",
    "load_eclipse",
]
