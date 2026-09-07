"""OS backends."""

def __getattr__(name: str):
    if name == "get_backend":
        from .factory import get_backend
        return get_backend
    if name == "OSBackend":
        from .base import OSBackend
        return OSBackend
    raise AttributeError(name)

__all__ = ["OSBackend", "get_backend"]
