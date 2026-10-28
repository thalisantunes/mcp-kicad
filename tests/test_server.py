"""Servidor MCP: `_guard` (erros viram ToolError) e handshake real por stdio.

O handshake sobe o servidor como processo filho e fala JSON-RPC, então cobre o
que os testes de função não enxergam: o contrato com o cliente (lista de tools
e `isError`). Não precisa de KiCad, porque o erro testado ocorre antes do
kicad-cli ser chamado.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from mcp_kicad import server
from mcp_kicad.errors import InvalidPath

EXPECTED_TOOL_COUNT = 17


def test_guard_turns_project_errors_into_tool_error() -> None:
    def fail():
        raise InvalidPath("arquivo não encontrado: /x")

    with pytest.raises(ToolError) as exc_info:
        server._guard(fail)

    assert str(exc_info.value) == "[MK-GEN-002] arquivo não encontrado: /x"


def test_guard_turns_unexpected_exceptions_into_mk_int_001() -> None:
    def boom():
        raise PermissionError("[Errno 13] Permission denied: '/x'")

    with pytest.raises(ToolError) as exc_info:
        server._guard(boom)

    assert str(exc_info.value) == "[MK-INT-001] PermissionError: [Errno 13] Permission denied: '/x'"
    assert isinstance(exc_info.value.__cause__, PermissionError)


def test_guard_passes_the_result_through() -> None:
    assert server._guard(lambda a, b=0: {"sum": a + b}, 1, b=2) == {"sum": 3}


def test_pcb_export_step_tool_forwards_subst_models(tmp_path, monkeypatch) -> None:
    seen = {}

    def fake_step(pcb, out_path, *, subst_models=True):
        seen.update(pcb=pcb, out_path=out_path, subst_models=subst_models)
        return Path(out_path)

    monkeypatch.setattr(server.kicad_cli, "pcb_export_step", fake_step)
    out = str(tmp_path / "board.step")

    assert server.pcb_export_step("a.kicad_pcb", out, subst_models=False) == {"path": out}
    assert seen == {"pcb": "a.kicad_pcb", "out_path": out, "subst_models": False}
    server.pcb_export_step("a.kicad_pcb", out)
    assert seen["subst_models"] is True


async def test_stdio_lists_all_tools(server_params) -> None:
    async with stdio_client(server_params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()

    names = [tool.name for tool in tools.tools]
    assert len(names) == EXPECTED_TOOL_COUNT
    assert len(set(names)) == len(names)
    assert "pcb_export_step" in names


async def test_stdio_project_errors_arrive_with_is_error_and_mk_code(server_params) -> None:
    async with stdio_client(server_params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("pcb_drc", {"pcb_path": "/nao/existe.kicad_pcb"})

    text = "".join(getattr(block, "text", "") for block in result.content)
    assert result.is_error is True
    assert "[MK-GEN-002]" in text
    assert "/nao/existe.kicad_pcb" in text
