"""Validation for network probes — one home, called at every backend.

Same structure as `os/terminal/allowlist.py`, `os/windows/geometry.py` and
`os/input/validation.py`: the rule lives in one module and each backend applies
it at the point that acts.

Why a host needs validating even without a shell
------------------------------------------------
These are the first endpoints that take a caller-supplied string and hand it to
an external program. There is no shell — `ping` is run as argv with
``shell=False`` — so this is not injection defence. It is **option injection**
defence, which argv does not solve on its own:

    ping -f              flood ping
    ping -c 1000000      a probe that does not stop
    ping -I eth0         a different interface than the operator expects

A hostname cannot begin with ``-``, so refusing that shape removes the whole
class. RFC 1123 does the rest: labels of letters, digits and hyphens, 63 bytes
each, 253 total.
"""
from __future__ import annotations

import ipaddress
import re

MAX_HOSTNAME = 253
MAX_LABEL = 63

PING_COUNT_MIN, PING_COUNT_MAX = 1, 10
PING_TIMEOUT_MIN, PING_TIMEOUT_MAX = 1, 10

# One DNS label: alphanumeric, hyphens inside only. Deliberately does not allow
# a leading hyphen — that is what makes an option unable to pose as a host.
_LABEL = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class NetworkRejected(Exception):
    """The request is not usable. Carries the reason shown to the caller."""


def validate_host(host: object) -> str:
    """An IP literal or a hostname. Anything else is refused.

    Returns the host unchanged (trailing dot stripped) so the caller passes on
    exactly what was validated, not a re-derived string.
    """
    if not isinstance(host, str):
        raise NetworkRejected(f"host must be a string, got {type(host).__name__}")
    name = host.strip()
    if not name:
        raise NetworkRejected("host must not be empty")
    if len(name) > MAX_HOSTNAME:
        raise NetworkRejected(f"host too long ({len(name)} > {MAX_HOSTNAME})")
    if name.startswith("-"):
        # The reason this check exists, stated where it is enforced.
        raise NetworkRejected(
            f"host {host!r} must not begin with '-': a leading hyphen makes it "
            "indistinguishable from a command-line option"
        )

    # An IP literal is a host, and ipaddress is stricter than any regex here —
    # it rejects 999.1.1.1 and 1.2.3 alike.
    try:
        ipaddress.ip_address(name)
        return name
    except ValueError:
        pass

    candidate = name[:-1] if name.endswith(".") else name  # a FQDN may end in '.'
    if not candidate:
        raise NetworkRejected("host must not be empty")

    # `999.999.999.999` is, strictly, a legal hostname: RFC 1123 allows
    # all-numeric labels, and `123.example.com` is a real name. But four numeric
    # labels is never a hostname anyone meant — it is a botched IP address, and
    # letting it through means the caller is told "could not resolve" when the
    # truth is "that is not a valid address". Same rule as everywhere else here:
    # report the real reason, not a plausible one.
    parts = candidate.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        raise NetworkRejected(
            f"{host!r} looks like an IPv4 address but is not a valid one "
            "(each octet must be 0-255)"
        )
    for label in candidate.split("."):
        if not label:
            raise NetworkRejected(f"host {host!r} has an empty label")
        if len(label) > MAX_LABEL:
            raise NetworkRejected(f"host {host!r} has a label longer than {MAX_LABEL}")
        if not _LABEL.match(label):
            raise NetworkRejected(f"host {host!r} is not a valid hostname or IP address")
    return candidate


def validate_ip(address: object) -> str:
    """Strictly an IP address — for reverse DNS, where a name makes no sense."""
    if not isinstance(address, str):
        raise NetworkRejected(f"address must be a string, got {type(address).__name__}")
    try:
        ipaddress.ip_address(address.strip())
    except ValueError as exc:
        raise NetworkRejected(f"{address!r} is not an IP address: {exc}") from exc
    return address.strip()


def validate_ping(count: object, timeout: object) -> tuple[int, int]:
    """Bounds for a probe that must stop on its own.

    `ping` with no count runs until it is killed. Both bounds are caps, not
    clamps: a request outside them is refused rather than quietly reduced to
    something the caller did not ask for.
    """
    if isinstance(count, bool) or not isinstance(count, int):
        raise NetworkRejected(f"count must be an integer, got {type(count).__name__}")
    if not PING_COUNT_MIN <= count <= PING_COUNT_MAX:
        raise NetworkRejected(
            f"count={count} out of range ({PING_COUNT_MIN}..{PING_COUNT_MAX})"
        )
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise NetworkRejected(f"timeout must be an integer, got {type(timeout).__name__}")
    if not PING_TIMEOUT_MIN <= timeout <= PING_TIMEOUT_MAX:
        raise NetworkRejected(
            f"timeout={timeout} out of range ({PING_TIMEOUT_MIN}..{PING_TIMEOUT_MAX})"
        )
    return count, timeout
