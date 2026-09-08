"""DNS lookups — one implementation, shared by every backend.

Deliberately NOT one copy per backend. `socket.getaddrinfo` is the same call on
Linux and on Windows: three copies of it could only differ by drifting, and the
repository has already been bitten by a fake backend that behaved differently
from the real ones. The backends expose `dns_resolve`/`dns_reverse` so callers
see a uniform surface, and delegate here.

Deliberately NOT `nslookup` or `dig` either. Shelling out would add a binary
dependency that minimal systems do not have — neither tool is installed on the
container this was written in — and would hand a caller-supplied string to a
command line for no benefit. The resolver is already in the standard library.
"""
from __future__ import annotations

import socket
from typing import Any

from windows_os_api.os.network.validation import (
    NetworkRejected,
    validate_host,
    validate_ip,
)


def resolve(host: str) -> dict[str, Any]:
    """Forward lookup. Returns every address the resolver reports, deduplicated."""
    try:
        name = validate_host(host)
    except NetworkRejected as exc:
        return {"ok": False, "error": str(exc), "host": host}

    try:
        infos = socket.getaddrinfo(name, None)
    except socket.gaierror as exc:
        # Not resolving is an ordinary outcome, not a crash: the caller asked a
        # question and the answer is "no such name".
        return {"ok": False, "error": f"could not resolve {name!r}: {exc}",
                "host": name, "addresses": []}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "host": name, "addresses": []}

    ipv4, ipv6 = [], []
    for info in infos:
        family, address = info[0], info[4][0]
        if family == socket.AF_INET and address not in ipv4:
            ipv4.append(address)
        elif family == socket.AF_INET6 and address not in ipv6:
            ipv6.append(address)
    return {
        "ok": bool(ipv4 or ipv6),
        "host": name,
        "addresses": ipv4 + ipv6,
        "ipv4": ipv4,
        "ipv6": ipv6,
    }


def reverse(address: str) -> dict[str, Any]:
    """Reverse lookup. Takes an IP only — a hostname here is a caller mistake."""
    try:
        ip = validate_ip(address)
    except NetworkRejected as exc:
        return {"ok": False, "error": str(exc), "address": address}

    try:
        hostname, aliases, addresses = socket.gethostbyaddr(ip)
    except (socket.herror, socket.gaierror) as exc:
        return {"ok": False, "error": f"no reverse record for {ip}: {exc}", "address": ip}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "address": ip}
    return {
        "ok": True,
        "address": ip,
        "hostname": hostname,
        "aliases": list(aliases),
        "addresses": list(addresses),
    }
