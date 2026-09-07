"""RBAC permissions — hard assertions."""
from windows_os_api.core.permissions.model import Permission, Role, has_permission, ROLE_PERMISSIONS

def test_admin_has_all():
    for p in Permission:
        assert has_permission(Role.ADMIN, p)

def test_viewer_cannot_write_fs():
    assert not has_permission(Role.VIEWER, Permission.FILESYSTEM_WRITE)
    assert has_permission(Role.VIEWER, Permission.FILESYSTEM_READ)

def test_operator_can_control_ui():
    assert has_permission(Role.OPERATOR, Permission.UI_CONTROL)
    assert not has_permission(Role.OPERATOR, Permission.ADMIN)

def test_role_permissions_non_empty():
    for role, perms in ROLE_PERMISSIONS.items():
        assert len(perms) > 0, role
