"""Runtime package."""

from .config import Settings, get_settings

def __getattr__(name: str):
    if name == "create_app":
        from .app import create_app
        return create_app
    raise AttributeError(name)

__all__ = ["Settings", "get_settings", "create_app"]
