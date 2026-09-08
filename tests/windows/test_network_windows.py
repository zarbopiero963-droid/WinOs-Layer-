"""Routes, DNS and ping against the real Windows host.

Same discipline as the Linux half: everything stays on loopback and reserved
names, so nothing here depends on the runner's internet connection.

    127.0.0.1 / localhost   always present
    invalid.invalid         RFC 2606, guaranteed never to resolve
    203.0.113.1             RFC 5737 TEST-NET-3, guaranteed unroutable
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.windows

if sys.platform != "win32":
    pytest.skip("requires real Windows", allow_module_level=True)

from windows_os_api.backends.windows import WindowsBackend  # noqa: E402


@pytest.fixture
def backend(tmp_path):
    return WindowsBackend(str(tmp_path))


# ---------------------------------------------------------------------------
# Routes — parsed from `route print`
# ---------------------------------------------------------------------------
def test_routes_come_back_parsed(backend):
    routes = backend.list_routes()
    assert isinstance(routes, list)
    assert routes, "a Windows host with networking has at least one route"
    for route in routes:
        assert set(route) >= {"interface", "destination", "gateway", "netmask",
                              "metric", "default", "up", "source"}
        assert route["source"] == "route print"


def test_route_addresses_are_real_addresses(backend):
    """Including the gateway, where Windows prints "On-link" rather than an IP.

    That word is normalised to 0.0.0.0 — the same thing Linux reports for a
    directly attached network — so a caller does not need a per-platform branch
    to read one field.
    """
    import ipaddress

    for route in backend.list_routes():
        for field in ("destination", "gateway", "netmask"):
            ipaddress.IPv4Address(route[field])


def test_the_default_route_is_labelled(backend):
    for route in backend.list_routes():
        if route["default"]:
            assert route["destination"] == "0.0.0.0"
            assert route["netmask"] == "0.0.0.0"


# ---------------------------------------------------------------------------
# DNS — the shared socket implementation, exercised on win32
# ---------------------------------------------------------------------------
def test_localhost_resolves_to_loopback(backend):
    result = backend.dns_resolve("localhost")
    assert result["ok"] is True, result
    assert "127.0.0.1" in result["addresses"] or "::1" in result["addresses"], result


def test_a_reserved_name_does_not_resolve(backend):
    result = backend.dns_resolve("invalid.invalid")
    assert result["ok"] is False, result
    assert result["addresses"] == []


def test_reverse_lookup_of_loopback(backend):
    result = backend.dns_reverse("127.0.0.1")
    assert result["ok"] is True, result
    assert result["hostname"], result


def test_reverse_lookup_refuses_a_hostname(backend):
    result = backend.dns_reverse("localhost")
    assert result["ok"] is False
    assert "is not an IP address" in result["error"]


# ---------------------------------------------------------------------------
# ping — different flags from Linux, which is the point of testing it here
# ---------------------------------------------------------------------------
def test_ping_loopback_gets_replies(backend):
    """Also pins the flag translation.

    Windows `ping` takes `-n` for the count and `-w` for a per-reply timeout in
    **milliseconds**. Passing the Linux `-W 2` here would mean a 2ms timeout,
    which fails against anything but loopback and reads as a network fault
    rather than as a bug.
    """
    result = backend.ping("127.0.0.1", count=2, timeout=2)
    assert result["ok"] is True, result
    assert result["transmitted"] == 2
    assert result["received"] in (1, 2), result


def test_ping_an_unroutable_address_fails_cleanly_and_bounded(backend):
    result = backend.ping("203.0.113.1", count=1, timeout=1)
    assert result["ok"] is False, result
    assert result["received"] in (0, None), result


@pytest.mark.parametrize("host", ["-f", "--flood", "-t", "-n"])
def test_ping_refuses_a_host_that_could_be_an_option(backend, host):
    """`ping -t` on Windows pings forever. argv does not stop that; this does."""
    result = backend.ping(host)
    assert result["ok"] is False, result
    assert "must not begin with '-'" in result["error"], result


@pytest.mark.parametrize("count,timeout", [(0, 2), (11, 2), (2, 0), (2, 11), ("2", 2)])
def test_ping_refuses_counts_and_timeouts_outside_their_bounds(backend, count, timeout):
    result = backend.ping("127.0.0.1", count=count, timeout=timeout)
    assert result["ok"] is False, result
