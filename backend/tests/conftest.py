"""Shared test setup.

The one rule enforced here: unit tests do not touch the network.

This is not hygiene for its own sake. test_slate_is_free_when_unconfigured
called the real endpoint, which fans out to ESPN across five leagues and 31
days. It was 19.9 seconds of a 20.8 second suite, and on one run it hung long
enough to take the whole thing to fifteen minutes. A test that fails because
someone else's API is slow tells you nothing about this code, and a suite that
sometimes takes fifteen minutes stops being run.

Blocking it at the socket layer rather than trusting everyone to remember is
the difference between a rule and a suggestion.
"""

import socket

import pytest

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _is_local(address) -> bool:
    """Loopback stays allowed: TestClient and sqlite are not the problem."""
    if not isinstance(address, tuple) or not address:
        return True          # unix sockets, odd families — not our concern
    host = str(address[0])
    return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0")


@pytest.fixture(autouse=True)
def no_outbound_network(monkeypatch, request):
    """Refuse outbound connections, with a message that says what to do.

    Opt out for a test that genuinely needs the network:

        @pytest.mark.network
        def test_against_the_real_api(): ...
    """
    if request.node.get_closest_marker("network"):
        return

    def guard(self, address, *a, **kw):
        if _is_local(address):
            return _real_connect(self, address, *a, **kw)
        raise RuntimeError(
            f"This test tried to reach {address!r}. Unit tests must not use "
            "the network — stub the client (see test_payments.py for an "
            "example), or mark the test @pytest.mark.network if it truly "
            "needs it."
        )

    def guard_ex(self, address, *a, **kw):
        if _is_local(address):
            return _real_connect_ex(self, address, *a, **kw)
        raise RuntimeError(f"This test tried to reach {address!r} (connect_ex).")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard_ex)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "network: test genuinely requires outbound network access")
