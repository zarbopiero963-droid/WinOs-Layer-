"""The network probe contract, in the places every backend has to honour it.

Real behaviour is covered where it is real — tests/linux against the kernel
routing table and the live resolver, tests/windows against `route print` and the
Windows `ping`. This suite covers what must hold identically everywhere:

* the validator refuses the same requests on every platform, before anything
  reaches the network;
* the three backends implement the same surface;
* the fake answers from fixtures and never touches the network, so the suite
  stays offline and deterministic.
"""
from __future__ import annotations

import pytest

from windows_os_api.backends.base import OSBackend
from windows_os_api.backends.fake import FakeBackend
from windows_os_api.os.network.validation import (
    NetworkRejected,
    validate_host,
    validate_ip,
    validate_ping,
)

PROBE_METHODS = ("list_routes", "dns_resolve", "dns_reverse", "ping")


# ---------------------------------------------------------------------------
# Option injection — the reason host validation exists
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("host", ["-f", "--flood", "-c", "-I", "-w", "-"])
def test_a_host_may_not_begin_with_a_hyphen(host):
    """argv stops shell injection. It does NOT stop option injection.

    `ping` is run as argv with shell=False, so nothing is interpreted by a
    shell — but `ping -f` is still a flood ping if `-f` arrives where a
    hostname is expected. A hostname cannot begin with '-', so refusing that
    shape removes the whole class rather than one instance of it.
    """
    with pytest.raises(NetworkRejected) as exc:
        validate_host(host)
    assert "must not begin with '-'" in str(exc.value) or "not a valid hostname" in str(exc.value)


@pytest.mark.parametrize(
    "host",
    ["localhost", "example.com", "sub.example.com", "example.com.",
     "127.0.0.1", "::1", "10.0.0.5", "a-b.example"],
)
def test_real_hosts_pass(host):
    assert validate_host(host)


@pytest.mark.parametrize(
    "host",
    ["", "   ", "a" * 300, "double..dot", "bad_underscore.example",
     "-leading.example", "trailing-.example",
     "sp ace.example", 42, None, ["localhost"]],
)
def test_hosts_that_are_not_hosts_are_refused(host):
    with pytest.raises(NetworkRejected):
        validate_host(host)


def test_a_label_longer_than_63_is_refused():
    with pytest.raises(NetworkRejected) as exc:
        validate_host("x" * 64 + ".example")
    assert "label longer than 63" in str(exc.value)


def test_a_botched_ip_is_named_as_one_not_as_an_unresolvable_name():
    """`999.999.999.999` is, strictly, a legal hostname.

    RFC 1123 allows all-numeric labels, and `123.example.com` is a real name —
    so the first version of this test, which expected a generic refusal, was
    asserting something untrue about DNS. But four numeric labels is never a
    hostname anyone meant, and letting it through would tell the caller "could
    not resolve" when the truth is "that is not a valid address".
    """
    with pytest.raises(NetworkRejected) as exc:
        validate_host("999.999.999.999")
    assert "looks like an IPv4 address" in str(exc.value)


def test_an_all_numeric_name_that_is_not_a_dotted_quad_still_passes():
    """The guard above must not swallow legitimate names.

    `123.example.com` and `1.2.3` are both valid hostnames; only the four-label
    all-numeric shape is treated as a botched address.
    """
    assert validate_host("123.example.com")
    assert validate_host("1.2.3")


# ---------------------------------------------------------------------------
# Reverse DNS takes an address
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "10.0.0.5"])
def test_ip_literals_pass(address):
    assert validate_ip(address) == address


@pytest.mark.parametrize("address", ["localhost", "example.com", "", "999.1.1.1", 42, None])
def test_reverse_refuses_anything_that_is_not_an_ip(address):
    with pytest.raises(NetworkRejected):
        validate_ip(address)


# ---------------------------------------------------------------------------
# ping bounds — a probe must stop on its own
# ---------------------------------------------------------------------------
def test_reasonable_bounds_pass():
    assert validate_ping(2, 2) == (2, 2)


@pytest.mark.parametrize(
    "count,timeout",
    [(0, 2), (-1, 2), (11, 2), (1000, 2), (2, 0), (2, 11),
     ("2", 2), (2, "2"), (True, 2), (2, True), (2.5, 2), (None, 2)],
)
def test_bounds_outside_the_range_are_refused_not_clamped(count, timeout):
    """Refused, not reduced.

    Clamping a request for 1000 pings down to 10 would run a probe the caller
    did not ask for and report it as success.
    """
    with pytest.raises(NetworkRejected):
        validate_ping(count, timeout)


# ---------------------------------------------------------------------------
# Every backend implements the same surface
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", PROBE_METHODS)
def test_the_protocol_declares_every_probe(name):
    assert hasattr(OSBackend, name), f"OSBackend is missing {name}"


@pytest.mark.parametrize("name", PROBE_METHODS)
def test_all_three_backends_implement_it(name):
    from windows_os_api.backends import fake, linux, windows

    for module, cls_name in (
        (fake, "FakeBackend"),
        (linux, "LinuxBackend"),
        (windows, "WindowsBackend"),
    ):
        cls = getattr(module, cls_name)
        assert callable(getattr(cls, name, None)), f"{cls_name} is missing {name}"


def test_dns_is_implemented_once_not_three_times():
    """`socket.getaddrinfo` is the same call on both platforms.

    Three copies could only differ by drifting, and this repository has already
    been bitten by a fake that behaved differently from the real backends. Both
    real backends delegate to the shared module; this fails if one grows its
    own copy.
    """
    import inspect

    from windows_os_api.backends import linux, windows

    for cls in (linux.LinuxBackend, windows.WindowsBackend):
        for method in ("dns_resolve", "dns_reverse"):
            source = inspect.getsource(getattr(cls, method))
            assert "_dns." in source, f"{cls.__name__}.{method} no longer delegates"
            assert "getaddrinfo" not in source, \
                f"{cls.__name__}.{method} grew its own resolver"


# ---------------------------------------------------------------------------
# The fake answers from fixtures and never touches the network
# ---------------------------------------------------------------------------
@pytest.fixture
def fake(tmp_path):
    return FakeBackend(str(tmp_path))


def test_fake_never_reaches_the_network(fake, monkeypatch):
    """If the fake resolved for real, the suite would stop being offline.

    Breaking `socket` outright is the way to prove it: anything that reached
    the resolver would raise here instead of quietly succeeding on a machine
    that happens to have DNS.
    """
    import socket

    def _explode(*args, **kwargs):
        raise AssertionError("the fake backend reached the real resolver")

    monkeypatch.setattr(socket, "getaddrinfo", _explode)
    monkeypatch.setattr(socket, "gethostbyaddr", _explode)

    assert fake.dns_resolve("localhost")["ok"] is True
    assert fake.dns_reverse("127.0.0.1")["ok"] is True
    assert fake.ping("127.0.0.1")["ok"] is True
    assert fake.list_routes()


def test_fake_applies_the_same_validation_as_the_real_backends(fake):
    assert fake.dns_resolve("-f")["ok"] is False
    assert fake.dns_reverse("localhost")["ok"] is False
    assert fake.ping("-f")["ok"] is False
    assert fake.ping("127.0.0.1", count=0)["ok"] is False
    assert fake.ping("127.0.0.1", count=99)["ok"] is False


def test_fake_routes_have_the_same_shape_as_real_ones(fake):
    for route in fake.list_routes():
        assert set(route) >= {"interface", "destination", "gateway", "netmask",
                              "metric", "default", "up", "source"}
    defaults = [r for r in fake.list_routes() if r["default"]]
    assert len(defaults) == 1
    assert defaults[0]["destination"] == "0.0.0.0"


def test_fake_reports_an_unreachable_host_as_unreachable(fake):
    result = fake.ping("203.0.113.1")
    assert result["ok"] is False
    assert result["received"] == 0


# ---------------------------------------------------------------------------
# Through the HTTP surface
# ---------------------------------------------------------------------------
def test_api_routes(client, auth_headers):
    body = client.get("/v1/network/routes", headers=auth_headers).json()
    assert isinstance(body["routes"], list)
    assert any(r["default"] for r in body["routes"])


def test_api_dns_resolve(client, auth_headers):
    body = client.post("/v1/network/dns/resolve", headers=auth_headers,
                       json={"host": "localhost"}).json()
    assert body["ok"] is True, body
    assert "127.0.0.1" in body["addresses"]


def test_api_dns_resolve_refuses_an_option_shaped_host(client, auth_headers):
    body = client.post("/v1/network/dns/resolve", headers=auth_headers,
                       json={"host": "-f"}).json()
    assert body["ok"] is False


def test_api_dns_reverse(client, auth_headers):
    body = client.post("/v1/network/dns/reverse", headers=auth_headers,
                       json={"address": "127.0.0.1"}).json()
    assert body["ok"] is True, body
    assert body["hostname"] == "localhost"


def test_api_ping_and_its_refusals(client, auth_headers):
    ok = client.post("/v1/network/ping", headers=auth_headers,
                     json={"host": "127.0.0.1", "count": 2}).json()
    assert ok["ok"] is True, ok
    bad = client.post("/v1/network/ping", headers=auth_headers,
                      json={"host": "127.0.0.1", "count": 1000}).json()
    assert bad["ok"] is False, bad


def test_api_probes_require_a_key(client):
    for path, payload in (
        ("/v1/network/dns/resolve", {"host": "localhost"}),
        ("/v1/network/ping", {"host": "127.0.0.1"}),
    ):
        assert client.post(path, json=payload).status_code in (401, 403)
    assert client.get("/v1/network/routes").status_code in (401, 403)
