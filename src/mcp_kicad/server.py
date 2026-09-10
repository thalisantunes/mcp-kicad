"""Servidor MCP.

Roteamento (ADR 0001): operação sobre arquivo → kicad_cli. Operação que precisa
da sessão aberta do PCB Editor → ipc. Nenhuma capacidade nos dois caminhos.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__, ipc, kicad_cli
from .errors import McpKicadError
from .fab import jlcpcb, rotations

# mcp>=2.0: MCPServer substituiu FastMCP (era mcp.server.fastmcp.FastMCP).
mcp = MCPServer("mcp-kicad", version=__version__)


def _guard(fn, *args, **kwargs) -> Any:
    """Converte erros do projeto em mensagem legível em vez de stack trace."""
    try:
        return fn(*args, **kwargs)
    except McpKicadError as exc:
        return {"error": str(exc)}


# --- Ambiente --------------------------------------------------------------


@mcp.tool()
def kicad_version() -> dict[str, Any]:
    """Versão do KiCad que o servidor está usando, via kicad-cli.

    Primeira coisa a chamar quando algo não funciona: confirma que o binário foi
    encontrado e qual instalação está em uso.
    """
    return _guard(lambda: {"version": kicad_cli.version()})


# --- Verificação (arquivo → kicad-cli) -------------------------------------


@mcp.tool()
def pcb_drc(pcb_path: str, all_severities: bool = False) -> dict[str, Any]:
    """Roda DRC no .kicad_pcb e devolve o relatório JSON do KiCad.

    Por padrão só erros. `all_severities=True` inclui warnings e exclusões.
    Usa as custom rules do projeto (.kicad_dru) — aplique um perfil com
    jlcpcb_apply_rules antes, se quiser validar contra a fábrica.
    """
    return _guard(kicad_cli.pcb_drc, pcb_path, all_severities=all_severities)


@mcp.tool()
def sch_erc(sch_path: str, all_severities: bool = False) -> dict[str, Any]:
    """Roda ERC no .kicad_sch e devolve o relatório JSON do KiCad."""
    return _guard(kicad_cli.sch_erc, sch_path, all_severities=all_severities)


# --- Export (arquivo → kicad-cli) ------------------------------------------


@mcp.tool()
def pcb_export_gerbers(pcb_path: str, outdir: str) -> dict[str, Any]:
    """Exporta Gerbers do .kicad_pcb para um diretório."""
    return _guard(lambda: {"outdir": str(kicad_cli.pcb_export_gerbers(pcb_path, outdir))})


@mcp.tool()
def pcb_export_drill(pcb_path: str, outdir: str) -> dict[str, Any]:
    """Exporta arquivos de furação (Excellon) do .kicad_pcb."""
    return _guard(lambda: {"outdir": str(kicad_cli.pcb_export_drill(pcb_path, outdir))})


@mcp.tool()
def sch_export_bom(sch_path: str, out_csv: str) -> dict[str, Any]:
    """Exporta BOM do esquemático em CSV.

    Formato genérico do KiCad, não o layout de colunas da JLCPCB.
    """
    return _guard(lambda: {"path": str(kicad_cli.sch_export_bom(sch_path, out_csv))})


@mcp.tool()
def sch_export_netlist(sch_path: str, out_path: str) -> dict[str, Any]:
    """Exporta netlist do esquemático."""
    return _guard(lambda: {"path": str(kicad_cli.sch_export_netlist(sch_path, out_path))})


# --- JLCPCB ----------------------------------------------------------------


@mcp.tool()
def jlcpcb_list_profiles() -> dict[str, Any]:
    """Perfis de design rules JLCPCB disponíveis, com o que cada um assume."""
    return {"profiles": jlcpcb.list_profiles(), "default": jlcpcb.DEFAULT_PROFILE}


@mcp.tool()
def jlcpcb_apply_rules(pcb_path: str, profile: str = jlcpcb.DEFAULT_PROFILE) -> dict[str, Any]:
    """Grava o perfil JLCPCB como <projeto>.kicad_dru ao lado do .kicad_pcb.

    ATENÇÃO: sobrescreve custom rules existentes do projeto — o KiCad só lê de
    um arquivo com esse nome exato. O anterior é salvo como .kicad_dru.bak.
    Rode pcb_drc depois para ver o resultado.
    """
    return _guard(lambda: {"rules_file": str(jlcpcb.apply_rules(pcb_path, profile))})


@mcp.tool()
def jlcpcb_show_rules(profile: str = jlcpcb.DEFAULT_PROFILE) -> dict[str, Any]:
    """Conteúdo de um perfil, sem escrever nada. Para inspecionar antes de aplicar."""
    return _guard(lambda: {"profile": profile, "rules": jlcpcb.rules_text(profile)})


@mcp.tool()
def jlcpcb_export_fab_package(pcb_path: str, outdir: str | None = None) -> dict[str, Any]:
    """Gera Gerber + Excellon e empacota no zip de upload da JLCPCB.

    Não inclui BOM/CPL — use jlcpcb_export_bom e jlcpcb_export_cpl.
    """
    return _guard(jlcpcb.export_fab_package, pcb_path, outdir)


@mcp.tool()
def jlcpcb_export_bom(sch_path: str, out_csv: str) -> dict[str, Any]:
    """Gera o BOM de SMT assembly da JLCPCB a partir do esquemático.

    Colunas: `Designator,Footprint,Quantity,Value,LCSC Part #`. O código LCSC
    é consolidado a partir dos vários nomes de campo que bibliotecas
    diferentes usam (LCSC, LCSC Part #, LCSC#, JLCPCB Part #) — a primeira
    não-vazia vence. `missing_lcsc` no retorno diz quantas linhas ficaram sem
    código: a JLCPCB rejeita SMT assembly sem ele.
    """
    return _guard(jlcpcb.export_bom, sch_path, out_csv)


@mcp.tool()
def jlcpcb_export_cpl(pcb_path: str, out_csv: str) -> dict[str, Any]:
    """Gera o CPL (posições de montagem) da JLCPCB a partir do PCB.

    Colunas: `Designator,Mid X,Mid Y,Rotation,Layer`. Aplica a correção de
    rotação por encapsulamento (SOT-23, DFN, QFN...) usando a tabela baixada
    por jlcpcb_fetch_rotations. Se a tabela não estiver em cache, o CPL sai
    SEM correção nenhuma e o retorno vem com `warning` explícito — rode
    jlcpcb_fetch_rotations antes de mandar para a fábrica. `corrections_applied`
    no retorno é o antes/depois por designator, para auditoria.
    """
    return _guard(jlcpcb.export_cpl, pcb_path, out_csv)


@mcp.tool()
def jlcpcb_fetch_rotations(force: bool = False) -> dict[str, Any]:
    """Baixa a tabela de correção de rotação de footprint para o cache local.

    Fonte: matthewlai/JLCKicadTools (GPL-3.0), não embarcada neste repo MIT —
    ver docs/rotacoes-cpl.md. Fica em `$XDG_CACHE_HOME/mcp-kicad/` (ou
    `~/.cache/mcp-kicad/`). `force=True` rebaixa mesmo se já houver cache.
    Rode antes de jlcpcb_export_cpl para que a correção seja aplicada.
    """
    return _guard(lambda: {"path": str(rotations.fetch_table(force=force))})


# --- Sessão viva (IPC) -----------------------------------------------------


@mcp.tool()
def board_summary() -> dict[str, Any]:
    """Contagem de footprints, tracks, vias e nets no board ABERTO no PCB Editor.

    Requer o KiCad rodando com um board aberto. Falha com MK-IPC-001 se não
    houver sessão — nesse caso use as tools de arquivo (pcb_*).
    """
    return _guard(ipc.board_summary)


def main() -> None:
    mcp.run()
