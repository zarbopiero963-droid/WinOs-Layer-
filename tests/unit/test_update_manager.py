"""Auto-update verify/backup/rollback."""
from pathlib import Path
from windows_os_api.update.manager import UpdateManager, UpdatePackage

def test_verify_apply_rollback(tmp_path):
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("v1", encoding="utf-8")
    mgr = UpdateManager(install, backup)
    assert mgr.version == "1.0.0"

    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "app.txt").write_text("v2", encoding="utf-8")
    checksum = UpdateManager.checksum(pkg_dir / "app.txt")
    # For directory packages, checksum the marker file we ship
    marker = pkg_dir / "app.txt"
    pkg = UpdatePackage(version="2.0.0", path=pkg_dir, checksum_sha256=UpdateManager.checksum(marker))
    # verify on directory: our verify checks package.path file checksum — for dir use file package
    file_pkg_src = tmp_path / "release.bin"
    file_pkg_src.write_bytes(b"NEWCONTENT")
    good = UpdatePackage(version="2.0.0", path=file_pkg_src, checksum_sha256=UpdateManager.checksum(file_pkg_src))
    assert mgr.verify(good) is True
    bad = UpdatePackage(version="2.0.0", path=file_pkg_src, checksum_sha256="0" * 64)
    assert mgr.verify(bad) is False

    result = mgr.apply(good)
    assert result["ok"] is True
    assert (install / "release.bin").read_bytes() == b"NEWCONTENT"
    assert mgr.version == "2.0.0"

    # mutate then rollback
    (install / "release.bin").write_bytes(b"CORRUPT")
    rb = mgr.rollback()
    assert rb["ok"] is True
    # backup was of v1 state without release.bin necessarily — ensure version restored
    assert mgr.version == "1.0.0"
