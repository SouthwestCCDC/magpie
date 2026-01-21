"""Magpie: Content-addressed artifact storage with mutable tags."""

from importlib.metadata import PackageNotFoundError, version

from magpie.config import MagpieSettings, get_settings

try:
    __version__ = version("magpie")
except PackageNotFoundError:
    # Fallback for development/editable installs
    __version__ = "0.0.0-dev"

__all__ = ["MagpieSettings", "get_settings", "__version__"]
