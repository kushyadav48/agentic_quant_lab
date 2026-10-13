"""Deterministic suite forbids real outbound connections; loopback tests are allowed."""
import socket
import pytest


@pytest.fixture(autouse=True)
def offline_connections(monkeypatch):
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def allowed(address):
        return isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost")

    def guarded(self, address):
        if not allowed(address):
            raise AssertionError("Real outbound provider connections are forbidden in deterministic tests")
        return connect(self, address)

    def guarded_ex(self, address):
        if not allowed(address):
            raise AssertionError("Real outbound provider connections are forbidden in deterministic tests")
        return connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_ex)
