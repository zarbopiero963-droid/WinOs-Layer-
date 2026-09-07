"""OS backends: WindowsBackend, LinuxBackend, FakeBackend."""

def __getattr__(name: str):
    if name == "get_backend":
        from .factory import get_backend
        return get_backend
    if name == "reset_backend":
        from .factory import reset_backend
        return reset_backend
    if name == "OSBackend":
        from .base import OSBackend
        return OSBackend
    if name == "LinuxBackend":
        from .linux import LinuxBackend
        return LinuxBackend
    if name == "WindowsBackend":
        from .windows import WindowsBackend
        return WindowsBackend
    if name == "FakeBackend":
        from .fake import FakeBackend
        return FakeBackend
    raise AttributeError(name)

__all__ = [
    "OSBackend",
    "get_backend",
    "reset_backend",
    "LinuxBackend",
    "WindowsBackend",
    "FakeBackend",
]
