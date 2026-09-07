"""RBAC permission model."""
from __future__ import annotations

from enum import Enum


class Permission(str, Enum):
    SYSTEM_READ = "system.read"
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    PROCESS_READ = "process.read"
    PROCESS_EXECUTE = "process.execute"
    UI_READ = "ui.read"
    UI_CONTROL = "ui.control"
    NETWORK_READ = "network.read"
    SERVICE_CONTROL = "service.control"
    ADMIN = "admin"
    REGISTRY_READ = "registry.read"
    REGISTRY_WRITE = "registry.write"
    TERMINAL_EXECUTE = "terminal.execute"
    ADAPTER_USE = "adapter.use"
    ADAPTER_MANAGE = "adapter.manage"


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    AUTOMATOR = "automator"
    ADMIN = "admin"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {
        Permission.SYSTEM_READ,
        Permission.FILESYSTEM_READ,
        Permission.PROCESS_READ,
        Permission.UI_READ,
        Permission.NETWORK_READ,
        Permission.REGISTRY_READ,
    },
    Role.OPERATOR: {
        Permission.SYSTEM_READ,
        Permission.FILESYSTEM_READ,
        Permission.FILESYSTEM_WRITE,
        Permission.PROCESS_READ,
        Permission.PROCESS_EXECUTE,
        Permission.UI_READ,
        Permission.UI_CONTROL,
        Permission.NETWORK_READ,
        Permission.SERVICE_CONTROL,
        Permission.REGISTRY_READ,
        Permission.ADAPTER_USE,
    },
    Role.AUTOMATOR: {
        Permission.SYSTEM_READ,
        Permission.FILESYSTEM_READ,
        Permission.FILESYSTEM_WRITE,
        Permission.PROCESS_READ,
        Permission.PROCESS_EXECUTE,
        Permission.UI_READ,
        Permission.UI_CONTROL,
        Permission.NETWORK_READ,
        Permission.SERVICE_CONTROL,
        Permission.REGISTRY_READ,
        Permission.REGISTRY_WRITE,
        Permission.TERMINAL_EXECUTE,
        Permission.ADAPTER_USE,
        Permission.ADAPTER_MANAGE,
    },
    Role.ADMIN: set(Permission),
}


def has_permission(role: Role, permission: Permission) -> bool:
    if role == Role.ADMIN:
        return True
    return permission in ROLE_PERMISSIONS.get(role, set())
