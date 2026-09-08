"""Routes, DNS and ping against the real Linux host.

Everything here stays on **loopback and reserved names**, deliberately:

* `127.0.0.1` and `localhost` always exist, on any runner and behind any proxy;
* `invalid.invalid` is reserved by RFC 2606 and is guaranteed never to resolve;
* `203.0.113.0/24` is TEST-NET-3 from RFC 5737 and is guaranteed unroutable.

A test that pinged `8.8.8.8` or resolved a real domain would be testing the
runner's internet connection, and would go red for a reason that has nothing to
do with this code. Offline and deterministic, as the repo's own rules require.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

pytestmark = pytest.mark.linux

if sys.platform == "win32":
    pytest.skip("Linux only", allow_module_level=True)


# ---------------------------------------------------------------------------
# Routes — read from /proc, so no binary is involved
# ---------------------------------------------------------------------------
def test_routes_come_back_from_the_kernel(linux_backend):
    routes = linux_backend.list_routes()
    assert isinstance(routes, list)
    assert routes, "a host with networking has at least one route"
    for route in routes:
        assert set(route) >= {"interface", "destination", "gateway", "netmask",
                              "metric", "default", "up", "source"}
        assert route["source"] == "/proc/net/route"


def test_route_addresses_are_parsed_not_left_as_hex(linux_backend):
    import ipaddress

    for route in linux_backend.list_routes():
        for field in ("destination", "gateway", "netmask"):
            ipaddress.IPv4Address(route[field])  # raises if still hex


@pytest.mark.parametrize(
    "hex_value,expected",
    [
        ("00000000", "0.0.0.0"),      # the default route's destination
        ("010200C0", "192.0.2.1"),    # C0 00 02 01 read back to front
        ("00FFFFFF", "255.255.255.0"),
        ("0100007F", "127.0.0.1"),
    ],
)
def test_the_hex_conversion_has_the_right_byte_order(hex_value, expected):
    """The parse that actually matters, pinned with known vectors.

    /proc/net/route stores addresses as hex in **host byte order** — little-
    endian on x86 — so `010200C0` is 192.0.2.1, the bytes read back to front.

    Asserting only that each field is a valid IPv4Address does NOT catch this,
    which is exactly what the block demo showed: with the endianness reversed,
    `010200C0` becomes `1.2.0.192`, a perfectly valid address that happens to
    be the wrong host. A plausible wrong answer is the failure mode worth
    testing for here, so the conversion is pinned directly instead.
    """
    from windows_os_api.backends.linux import _hex_le_to_ip

    assert _hex_le_to_ip(hex_value) == expected


def test_netmasks_are_contiguous(linux_backend):
    """A real netmask is a run of ones followed by a run of zeros.

    `255.255.255.0` is; `0.255.255.255` — the same mask with its bytes reversed
    — is not. `ipaddress` accepts both (it reads the second as a hostmask), so
    the property has to be asserted directly.
    """
    import ipaddress
    import re

    for route in linux_backend.list_routes():
        bits = f"{int(ipaddress.IPv4Address(route['netmask'])):032b}"
        assert re.fullmatch(r"1*0*", bits), (route["netmask"], bits, route)


def test_the_default_route_is_labelled(linux_backend):
    routes = linux_backend.list_routes()
    defaults = [r for r in routes if r["default"]]
    for route in defaults:
        assert route["destination"] == "0.0.0.0"
        assert route["netmask"] == "0.0.0.0"
    # Not asserting that a default route EXISTS: a container without one is a
    # legitimate host, and this suite must not depend on the runner's topology.
    non_defaults = [r for r in routes if not r["default"]]
    for route in non_defaults:
        assert not (route["destination"] == "0.0.0.0" and route["netmask"] == "0.0.0.0")


# ---------------------------------------------------------------------------
# DNS — socket, no subprocess
# ---------------------------------------------------------------------------
def test_localhost_resolves_to_loopback(linux_backend):
    result = linux_backend.dns_resolve("localhost")
    assert result["ok"] is True, result
    assert "127.0.0.1" in result["addresses"] or "::1" in result["addresses"], result


def test_a_reserved_name_does_not_resolve(linux_backend):
    """`invalid.invalid` — RFC 2606 guarantees this never resolves.

    Failing to resolve is an ordinary answer, not a crash: the caller asked a
    question and the answer is "no such name".
    """
    result = linux_backend.dns_resolve("invalid.invalid")
    assert result["ok"] is False, result
    assert "could not resolve" in result["error"]
    assert result["addresses"] == []


def test_reverse_lookup_of_loopback(linux_backend):
    result = linux_backend.dns_reverse("127.0.0.1")
    assert result["ok"] is True, result
    assert result["hostname"], result


def test_reverse_lookup_refuses_a_hostname(linux_backend):
    """Reverse DNS takes an address. A name here is a caller mistake, not input."""
    result = linux_backend.dns_reverse("localhost")
    assert result["ok"] is False
    assert "is not an IP address" in result["error"]


@pytest.mark.parametrize(
    "host", ["-f", "--flood", "", "   ", "a" * 300, "bad_underscore.example",
             "double..dot", "999.999.999.999"]
)
def test_dns_refuses_hosts_that_are_not_hosts(linux_backend, host):
    result = linux_backend.dns_resolve(host)
    assert result["ok"] is False, result


# ---------------------------------------------------------------------------
# ping — the only one that needs a binary
# ---------------------------------------------------------------------------
def _require_ping() -> None:
    """Skip when the binary is absent — but fail where CI says it is installed.

    Same rule as WINOS_REQUIRE_WM: a skipped test reads exactly like a passing
    one, so where the workflow installs `iputils-ping` a skip would mean the
    install broke and the coverage quietly went away.
    """
    if shutil.which("ping"):
        return
    message = "ping binary not installed (iputils-ping)"
    if os.environ.get("WINOS_REQUIRE_PING") == "1":
        pytest.fail(message + " (WINOS_REQUIRE_PING=1)")
    pytest.skip(message)


def test_ping_loopback_gets_replies(linux_backend):
    _require_ping()
    result = linux_backend.ping("127.0.0.1", count=2, timeout=2)
    assert result["ok"] is True, result
    assert result["transmitted"] == 2
    # `received` is parsed from the summary; None means it could not be read,
    # which is reported honestly rather than as 0.
    assert result["received"] in (1, 2), result


def test_ping_an_unroutable_address_fails_cleanly_and_bounded(linux_backend):
    """TEST-NET-3, RFC 5737 — reserved, and guaranteed not to answer.

    The point is that it FAILS rather than hanging: `ping` with no count runs
    until something kills it, and this is reachable over HTTP.
    """
    _require_ping()
    result = linux_backend.ping("203.0.113.1", count=1, timeout=1)
    assert result["ok"] is False, result
    assert result["received"] in (0, None), result


def test_ping_reports_honestly_when_the_binary_is_absent(linux_backend, monkeypatch):
    """No binary is a different answer from "the host is down".

    Reporting the second when the first is true would send an operator looking
    at the network instead of at their package list.
    """
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = linux_backend.ping("127.0.0.1")
    assert result["ok"] is False
    assert result["available"] is False
    assert "not installed" in result["error"]


@pytest.mark.parametrize("host", ["-f", "--flood", "-I", "-w"])
def test_ping_refuses_a_host_that_could_be_an_option(linux_backend, host):
    """Option injection, which argv does not solve on its own.

    There is no shell here, but `ping -f` is still a flood ping if `-f` arrives
    where a hostname is expected. A hostname cannot begin with '-', so refusing
    that shape removes the class.
    """
    result = linux_backend.ping(host)
    assert result["ok"] is False, result
    assert "must not begin with '-'" in result["error"], result


@pytest.mark.parametrize("count,timeout", [(0, 2), (11, 2), (2, 0), (2, 11),
                                           ("2", 2), (True, 2)])
def test_ping_refuses_counts_and_timeouts_outside_their_bounds(linux_backend,
                                                               count, timeout):
    """Refused, not clamped: a probe that runs longer than asked is not a favour."""
    result = linux_backend.ping("127.0.0.1", count=count, timeout=timeout)
    assert result["ok"] is False, result
    assert "out of range" in result["error"] or "must be an integer" in result["error"]
