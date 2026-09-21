"""N049 — risoluzione path handle-safe sotto la sandbox.

Il backend faceva ``Path.resolve()`` (segue i symlink) e poi apriva il file
senza ``O_NOFOLLOW``. Fra i due passi un contenditore puo' sostituire il file
con un symlink verso ``/tmp/...``: ``ok=True`` e la scrittura esce dalla
sandbox (falso successo riprodotto in Phase 0).

Qui ogni componente si attraversa con ``lstat`` / ``open(..., O_NOFOLLOW)``.
Un symlink intermedio o finale e' un rifiuto, non un seguito. Dopo l'open si
riverifica via ``/proc/self/fd/N`` (POSIX) che l'inode aperto stia ancora sotto
la radice consentita — cosi' anche un rename race post-open non conta come
successo.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Any

PATH_OUTSIDE_SANDBOX = "PATH_OUTSIDE_SANDBOX"
PATH_SYMLINK_REFUSED = "PATH_SYMLINK_REFUSED"
PATH_TRAVERSAL = "PATH_TRAVERSAL"
PATH_INVALID = "PATH_INVALID"
PATH_NOT_FOUND = "PATH_NOT_FOUND"


class PathRejected(PermissionError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _is_within(child: Path, root: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (ValueError, OSError):
        return False



def _looks_absolute(path: str) -> bool:
    """Assoluto host *o* forma POSIX ``/…`` anche su Windows.

    Su Win32 ``Path('/etc/shadow').is_absolute()`` e' False (manca il drive):
    senza questo i test di sicurezza vedrebbero 404 sotto la sandbox invece
    di 403 PATH_OUTSIDE_SANDBOX.
    """
    if not path:
        return False
    if Path(path).is_absolute():
        return True
    if path.startswith("/") and not path.startswith("//"):
        return True
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        return True
    return False


def normalize_user_path(path: str) -> str:
    if not isinstance(path, str) or path == "":
        raise PathRejected("path vuoto o non stringa", code=PATH_INVALID)
    # Blocca formati che non appartengono a un FS sandbox POSIX/locale.
    if path.startswith("\\\\") or path.startswith("//"):
        raise PathRejected(
            f"percorso UNC/network rifiutato: {path!r}", code=PATH_TRAVERSAL
        )
    if path.startswith("\\\\?\\") or path.startswith("\\??\\"):
        raise PathRejected(
            f"percorso device/NT rifiutato: {path!r}", code=PATH_TRAVERSAL
        )
    # Percent-encoding non si decodifica: un nome letterale "%2e%2e" non e'
    # traversal, ma accettare una decode silenziosa lo diventerebbe.
    if ".." in path.replace("\\", "/").split("/"):
        raise PathRejected(f"traversal rifiutato: {path!r}", code=PATH_TRAVERSAL)
    if "\x00" in path:
        raise PathRejected("NUL nel path", code=PATH_INVALID)
    return path.replace("\\", "/")


def split_relative(path: str) -> list[str]:
    norm = normalize_user_path(path)
    p = Path(norm)
    if p.is_absolute():
        # Gli assoluti si accettano solo se poi risultano sotto la sandbox;
        # li scomponiamo rispetto alla root in `walk_under`.
        return [norm]
    parts = [x for x in Path(norm).parts if x not in ("", ".")]
    if any(x == ".." for x in parts):
        raise PathRejected(f"traversal rifiutato: {path!r}", code=PATH_TRAVERSAL)
    return parts


def walk_under(root: Path, path: str) -> Path:
    """Risolve ``path`` sotto ``root`` senza seguire symlink.

    Ogni componente deve essere una directory reale (non symlink), l'ultimo
    puo' non esistere ancora (per create). Se un componente e' symlink →
    ``PATH_SYMLINK_REFUSED``.
    """
    root = root.resolve(strict=False)
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve(strict=True)

    norm = normalize_user_path(path)
    if _looks_absolute(norm):
        # Assoluto (anche forma POSIX su Windows): deve gia' giacere sotto root.
        abs_path = Path(norm)
        try:
            rel = abs_path.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise PathRejected(
                f"path fuori sandbox: {path!r}", code=PATH_OUTSIDE_SANDBOX
            ) from exc
        parts = [x for x in rel.parts if x not in ("", ".")]
    else:
        parts = split_relative(norm)

    current = root
    for i, part in enumerate(parts):
        nxt = current / part
        last = i == len(parts) - 1
        try:
            st = os.lstat(nxt)
        except FileNotFoundError:
            if last:
                # Destinazione da creare: il parent e' gia' verificato.
                return nxt
            raise PathRejected(
                f"componente mancante: {nxt}", code=PATH_NOT_FOUND
            ) from None
        if stat.S_ISLNK(st.st_mode):
            raise PathRejected(
                f"symlink rifiutato al componente {part!r} di {path!r}",
                code=PATH_SYMLINK_REFUSED,
            )
        if not last and not stat.S_ISDIR(st.st_mode):
            raise PathRejected(
                f"componente non directory: {part!r}", code=PATH_INVALID
            )
        current = nxt
    return current


def open_nofollow(
    path: Path,
    *,
    write: bool = False,
    create: bool = False,
    truncate: bool = False,
    mode: int = 0o644,
) -> int:
    """Apre ``path`` senza seguire un symlink finale."""
    # O_NOFOLLOW / O_CLOEXEC non esistono su Win32: getattr(..., 0) da solo
    # non basta — rifiutiamo il leaf symlink via lstat prima dell'open.
    flags = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    if not getattr(os, "O_NOFOLLOW", 0):
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            st = None
        if st is not None and stat.S_ISLNK(st.st_mode):
            raise PathRejected(
                f"symlink rifiutato all'apertura di {path}",
                code=PATH_SYMLINK_REFUSED,
            )
    if write:
        flags |= os.O_WRONLY
        if create:
            flags |= os.O_CREAT
        if truncate:
            flags |= os.O_TRUNC
    else:
        flags |= os.O_RDONLY
    try:
        return os.open(path, flags, mode)
    except OSError as exc:
        # Su Linux ELOOP = symlink con O_NOFOLLOW.
        if getattr(exc, "errno", None) in {errno_ELOOP(), errno_EPERM()}:
            raise PathRejected(
                f"symlink rifiutato all'apertura di {path}",
                code=PATH_SYMLINK_REFUSED,
            ) from exc
        raise


def errno_ELOOP() -> int:
    return getattr(__import__("errno"), "ELOOP", 40)


def errno_EPERM() -> int:
    return getattr(__import__("errno"), "EPERM", 1)


def _win32_final_path(fd: int) -> Path | None:
    """Best-effort GetFinalPathNameByHandleW; None se non disponibile."""
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes
    except ImportError:
        return None
    try:
        handle = msvcrt.get_osfhandle(fd)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        GetFinalPathNameByHandleW = kernel32.GetFinalPathNameByHandleW
        GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        GetFinalPathNameByHandleW.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(4096)
        n = GetFinalPathNameByHandleW(handle, buf, 4096, 0)
        if n == 0 or n >= 4096:
            return None
        final = buf.value
        unc_prefix = chr(92) + chr(92) + "?" + chr(92)  # \\?\
        if final.startswith(unc_prefix):
            final = final[4:]
        return Path(final)
    except OSError:
        return None


def fd_still_under(fd: int, root: Path) -> bool:
    """Riverifica che l'fd aperto punti ancora sotto ``root``.

    Su POSIX usa ``/proc/self/fd/N``. Su Win32 usa GetFinalPathNameByHandleW;
    se l'handle check non e' disponibile → fail-closed (False).
    """
    root_r = root.resolve(strict=False)
    if sys.platform == "win32":
        target = _win32_final_path(fd)
        if target is None:
            return False
        try:
            # Win32: Path.relative_to e' case-sensitive; normalizziamo.
            t = Path(os.path.normcase(str(target.resolve(strict=False))))
            r = Path(os.path.normcase(str(root_r)))
            t.relative_to(r)
            return True
        except (ValueError, OSError):
            return False
    proc = Path(f"/proc/self/fd/{fd}")
    if not proc.exists():
        return False
    try:
        target = Path(os.readlink(proc))
    except OSError:
        return False
    try:
        target.resolve(strict=False).relative_to(root_r)
        return True
    except (ValueError, OSError):
        return False


def read_bytes_nofollow(root: Path, path: str, max_bytes: int) -> tuple[Path, bytes]:
    target = walk_under(root, path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    fd = open_nofollow(target, write=False)
    try:
        if not fd_still_under(fd, root):
            raise PathRejected(
                f"fd fuori sandbox dopo open: {path!r}", code=PATH_OUTSIDE_SANDBOX
            )
        data = os.read(fd, max(0, int(max_bytes)))
        return target, data
    finally:
        os.close(fd)


def write_bytes_nofollow(root: Path, path: str, data: bytes) -> Path:
    target = walk_under(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Controlla symlink *prima* di exists() (che seguirebbe il target).
    if target.is_symlink():
        raise PathRejected(
            f"symlink rifiutato: {path!r}", code=PATH_SYMLINK_REFUSED
        )
    fd = open_nofollow(
        target, write=True, create=True, truncate=True
    )
    try:
        # Su Win32 senza O_NOFOLLOW: se tra check e open e' diventato symlink
        # abbiamo seguito il target — non scrivere, chiudi e rifiuta.
        if not getattr(os, "O_NOFOLLOW", 0) and target.is_symlink():
            raise PathRejected(
                f"symlink rifiutato dopo open: {path!r}", code=PATH_SYMLINK_REFUSED
            )
        if not fd_still_under(fd, root):
            raise PathRejected(
                f"fd fuori sandbox dopo open: {path!r}", code=PATH_OUTSIDE_SANDBOX
            )
        os.write(fd, data)
        return target
    finally:
        os.close(fd)


def unlink_nofollow(root: Path, path: str) -> Path:
    target = walk_under(root, path)
    if target.is_symlink():
        raise PathRejected(
            f"symlink rifiutato in delete: {path!r}", code=PATH_SYMLINK_REFUSED
        )
    if not target.exists():
        return target
    if target.is_dir() and not target.is_symlink():
        target.rmdir()
    else:
        # unlink non segue symlink sul nome finale su POSIX, ma abbiamo gia'
        # rifiutato i symlink sopra per contratto esplicito.
        target.unlink()
    return target


def lstat_under(root: Path, path: str) -> dict[str, Any]:
    target = walk_under(root, path)
    st = os.lstat(target)
    if stat.S_ISLNK(st.st_mode):
        raise PathRejected(
            f"symlink rifiutato in stat: {path!r}", code=PATH_SYMLINK_REFUSED
        )
    return {
        "path": path,
        "abs_path": str(target),
        "size": st.st_size,
        "is_dir": stat.S_ISDIR(st.st_mode),
        "is_file": stat.S_ISREG(st.st_mode),
        "mode": stat.S_IMODE(st.st_mode),
        "mtime": st.st_mtime,
        "ctime": st.st_ctime,
        "inode": st.st_ino,
    }


def hash_under(root: Path, path: str, *, algo: str = "sha256") -> dict[str, Any]:
    import hashlib

    if algo not in hashlib.algorithms_available:
        raise PathRejected(f"algoritmo hash non supportato: {algo}", code=PATH_INVALID)
    target, data = read_bytes_nofollow(root, path, max_bytes=64 * 1024 * 1024)
    h = hashlib.new(algo)
    h.update(data)
    # Se il file e' piu' grande del tetto, rileggi a chunk via fd nofollow.
    size = os.lstat(target).st_size
    if size > len(data):
        fd = open_nofollow(target, write=False)
        try:
            if not fd_still_under(fd, root):
                raise PathRejected(
                    f"fd fuori sandbox: {path!r}", code=PATH_OUTSIDE_SANDBOX
                )
            h = hashlib.new(algo)
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        finally:
            os.close(fd)
    return {
        "path": path,
        "algo": algo,
        "hex": h.hexdigest(),
        "size": size,
    }
