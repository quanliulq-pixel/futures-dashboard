import socket

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def deny(*a, **k):
        raise OSError("network disabled in hardening tests")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
