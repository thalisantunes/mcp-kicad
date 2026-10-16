"""Wrapper sobre a IPC API do KiCad (`kipy`).

Caminho secundário: exige o PCB Editor **aberto**. Serve para ler e mutar o
board que o usuário está olhando na tela.

Limites que valem para KiCad 9 e 10, e que este módulo não tenta esconder:

- só o editor de PCB tem IPC. Esquemático não. Use kicad_cli.
- não há plot/export via IPC. Use kicad_cli.
- não há modo headless (`kicad-cli api-server` só chega no KiCad 11), então
  `kipy.KiCad(headless=True)` falha aqui.

Regra de roteamento do projeto: arquivo → kicad_cli; sessão viva → este módulo.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from .errors import IpcUnavailable, UnsupportedByVersion

if TYPE_CHECKING:  # pragma: no cover
    from kipy import KiCad
    from kipy.board import Board


# O kipy escolhe o socket sozinho: no macOS e no Linux costuma ser /tmp/kicad/api.sock, no
# Windows e no Flatpak é outro caminho. Por isso a mensagem só dá o caminho como exemplo.
_DEFAULT_SOCKET = (
    "o socket padrão do kipy (por exemplo ipc:///tmp/kicad/api.sock no macOS e no Linux)"
)


def _socket_from_env() -> str | None:
    """`KICAD_API_SOCKET` com o prefixo `ipc://` que o nng exige.

    O KiCad injeta a variável já com o prefixo; quem a define à mão costuma
    esquecê-lo, e aí a conexão falha com "Invalid argument".
    """
    raw = os.environ.get("KICAD_API_SOCKET")
    if raw and "://" not in raw:
        return f"ipc://{raw}"
    return raw or None


def connect(timeout_ms: int = 2000) -> KiCad:
    """Conecta na sessão aberta do KiCad.

    Lê `KICAD_API_SOCKET` e `KICAD_API_TOKEN` do ambiente — o KiCad injeta essas
    variáveis nos plugins que ele lança. Rodando fora desse contexto (por
    exemplo, como MCP server iniciado pelo Claude Code), vale o socket padrão do
    kipy: `/tmp/kicad/api.sock` no macOS e no Linux (Windows e Flatpak usam outro
    caminho), e uma segunda instância do KiCad usa `api-<pid>.sock`. A API vem
    desligada: liga em Preferences → Plugins → "Enable KiCad API".
    """
    try:
        from kipy import KiCad as _KiCad
    except ImportError as exc:  # pragma: no cover
        raise IpcUnavailable(
            "kicad-python não instalado. `uv pip install kicad-python`"
        ) from exc

    socket_path = _socket_from_env()
    try:
        kicad = _KiCad(socket_path=socket_path, timeout_ms=timeout_ms)
        # O kipy conecta de forma preguiçosa: sem um pedido real, o KiCad fechado ou
        # com a API desligada só apareceria depois, disfarçado de "nenhum board aberto".
        kicad.get_version()
    except Exception as exc:
        raise IpcUnavailable(
            f"não foi possível conectar na IPC API do KiCad ({type(exc).__name__}: {exc}). "
            "O PCB Editor está aberto? A API está habilitada em Preferences → Plugins → "
            '"Enable KiCad API"? '
            f"Socket: {socket_path or _DEFAULT_SOCKET}"
        ) from exc
    return kicad


def open_board(timeout_ms: int = 2000) -> Board:
    """Board atualmente aberto no PCB Editor."""
    kicad = connect(timeout_ms=timeout_ms)
    try:
        return kicad.get_board()
    except Exception as exc:
        raise IpcUnavailable(
            "nenhum board aberto no PCB Editor, ou a versão do KiCad não expõe "
            "o board via IPC."
        ) from exc


def board_summary(timeout_ms: int = 2000) -> dict[str, Any]:
    """Contagem do que está no board aberto.

    Barato, e serve de smoke test da conexão IPC antes de qualquer mutação.
    """
    board = open_board(timeout_ms=timeout_ms)
    return {
        "footprints": len(board.get_footprints()),
        "tracks": len(board.get_tracks()),
        "vias": len(board.get_vias()),
        "nets": len(board.get_nets()),
    }


def get_schematic() -> Any:  # pragma: no cover — barreira intencional
    """Não disponível. Presente para dar erro claro em vez de AttributeError.

    O `kipy` 0.7.1 (PyPI) não tem `get_schematic` — só a doc da branch main
    lista o método. Medido em 12/08/2026, ver docs/probe-nightly-10.99.md.
    Para esquemático, use kicad_cli.
    """
    raise UnsupportedByVersion(
        "IPC não suporta esquemático. Use as tools sch_* que passam por "
        "kicad-cli."
    )


def export_via_ipc() -> Any:  # pragma: no cover — barreira intencional
    """Não disponível no KiCad 10. Ver kicad_cli.pcb_export_*."""
    raise UnsupportedByVersion(
        "plot/export via IPC só existe a partir do KiCad 11. Use as tools "
        "pcb_export_* que passam por kicad-cli."
    )
