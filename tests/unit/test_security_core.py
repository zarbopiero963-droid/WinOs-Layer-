"""Security: audit, rate limit, auth helpers."""
from windows_os_api.core.security.audit import AuditLogger
from windows_os_api.core.security.rate_limit import RateLimiter
from windows_os_api.core.permissions.model import Role, Permission, has_permission

def test_audit_logger_persists(tmp_path):
    path = tmp_path / "a.jsonl"
    log = AuditLogger(path)
    e = log.log("test.action", subject="u1", resource="r", detail={"x": 1})
    assert e["action"] == "test.action"
    entries = log.read_all()
    assert len(entries) == 1
    assert entries[0]["detail"]["x"] == 1

def test_rate_limiter():
    rl = RateLimiter(limit_per_minute=3)
    assert rl.allow("a")
    assert rl.allow("a")
    assert rl.allow("a")
    assert not rl.allow("a")
    assert rl.allow("b")  # different key
    assert rl.remaining("a") == 0

def test_has_permission_admin():
    assert has_permission(Role.ADMIN, Permission.TERMINAL_EXECUTE)
