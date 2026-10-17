"""Testes de ipc.connect sem KiCad aberto: o kipy.KiCad é substituído por um fake.

O kipy só conecta no primeiro pedido, então construir o cliente nunca falha. Sem
forçar a conexão, a mensagem de diagnóstico de `connect` jamais aparece.
"""

from __future__ import annotations

import pytest
from kipy.errors import ConnectionError as KipyConnectionError

from mcp_kicad import ipc
from mcp_kicad.errors import IpcUnavailable


def _install_fake_kicad(monkeypatch, *, fails: bool) -> list:
    created: list = []

    class FakeKiCad:
        def __init__(self, socket_path=None, client_name=None, kicad_token=None, timeout_ms=2000):
            self.socket_path = socket_path
            self.timeout_ms = timeout_ms
            created.append(self)

        def get_version(self):
            if fails:
                raise KipyConnectionError("Failed to connect to KiCad: Connection refused")
            return "10.0.5"

    monkeypatch.setattr("kipy.KiCad", FakeKiCad)
    return created


def test_connect_failure_explains_how_to_enable_the_api(monkeypatch) -> None:
    monkeypatch.delenv("KICAD_API_SOCKET", raising=False)
    _install_fake_kicad(monkeypatch, fails=True)

    with pytest.raises(IpcUnavailable) as exc_info:
        ipc.connect()

    message = str(exc_info.value)
    assert message.startswith("[MK-IPC-001]")
    assert "Enable KiCad API" in message
    assert "Preferences" in message
    assert "Connection refused" in message  # a causa real, não só "nenhum board aberto"
    assert "Socket:" in message
    assert "socket padrão do kipy" in message  # sem env: não afirma um caminho que pode não valer
    assert isinstance(exc_info.value.__cause__, KipyConnectionError)


def test_connect_failure_message_shows_the_socket_that_was_tried(monkeypatch) -> None:
    monkeypatch.setenv("KICAD_API_SOCKET", "/tmp/kicad/api-99.sock")
    _install_fake_kicad(monkeypatch, fails=True)

    with pytest.raises(IpcUnavailable) as exc_info:
        ipc.connect()

    assert "ipc:///tmp/kicad/api-99.sock" in str(exc_info.value)


def test_connect_prefixes_a_bare_socket_path_with_ipc_scheme(monkeypatch) -> None:
    monkeypatch.setenv("KICAD_API_SOCKET", "/tmp/kicad/api.sock")
    created = _install_fake_kicad(monkeypatch, fails=False)

    ipc.connect()

    assert created[0].socket_path == "ipc:///tmp/kicad/api.sock"


def test_connect_keeps_a_socket_that_already_has_the_scheme(monkeypatch) -> None:
    monkeypatch.setenv("KICAD_API_SOCKET", "ipc:///tmp/kicad/api-1234.sock")
    created = _install_fake_kicad(monkeypatch, fails=False)

    ipc.connect()

    assert created[0].socket_path == "ipc:///tmp/kicad/api-1234.sock"


def test_connect_without_env_leaves_the_default_socket_to_kipy(monkeypatch) -> None:
    monkeypatch.delenv("KICAD_API_SOCKET", raising=False)
    created = _install_fake_kicad(monkeypatch, fails=False)

    kicad = ipc.connect()

    assert kicad is created[0]
    assert created[0].socket_path is None
