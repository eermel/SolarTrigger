#!/usr/bin/env python3
"""Registre des plugins GPS."""
from .base import GpsPlugin, GpsPosition, GpsStatus


def available_plugins():
    from .gpsd import GpsdPlugin
    from .serial_nmea import SerialNmeaGps
    return {
        SerialNmeaGps.plugin_id: SerialNmeaGps,
        GpsdPlugin.plugin_id: GpsdPlugin,
    }


def load_plugin(plugin_id, log_fn=print, config=None):
    plugins = available_plugins()
    if plugin_id not in plugins:
        raise ValueError(f"Plugin GPS inconnu: {plugin_id}")
    return plugins[plugin_id](log_fn=log_fn, config=config)


__all__ = ["GpsPlugin", "GpsPosition", "GpsStatus", "available_plugins", "load_plugin"]
