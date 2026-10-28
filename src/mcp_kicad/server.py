"""Servidor MCP.

Roteamento (ADR 0001): operação sobre arquivo → kicad_cli. Operação que precisa
da sessão aberta do PCB Editor → ipc. Nenhuma capacidade nos dois caminhos.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__, ipc, kicad_cli
from .errors import InternalError, McpKicadError
from .fab import jlcpcb, rotations
from .validate import run_validate

# mcp>=2.0: MCPServer substituiu FastMCP (era mcp.server.fastmcp.FastMCP).
mcp = MCPServer("mcp-kicad", version=__version__)


def _guard(fn, *args, **kwargs) -> Any:
    """Converte toda falha em ToolError: o cliente recebe `isError=true` e a causa.

    Erro do projeto já traz o código MK-*. Qualquer outra exceção vira MK-INT-001
    com tipo e mensagem, porque o SDK (mcp>=2.3) esconderia a causa e devolveria
    só "Error executing tool X".
    """
    try:
        return fn(*args, **kwargs)
    except McpKicadError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(str(InternalError(f"{type(exc).__name__}: {exc}"))) from exc


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
def pcb_drc(
    pcb_path: str, all_severities: bool = False, report_path: str | None = None
) -> dict[str, Any]:
    """Roda DRC no .kicad_pcb e devolve um resumo do relatório.

    Por padrão só erros. `all_severities=True` inclui warnings e exclusões.
    Usa as custom rules do projeto (.kicad_dru) — aplique um perfil com
    jlcpcb_apply_rules antes, se quiser validar contra a fábrica.

    Retorno: `violations` (total), `by_severity`, `first` (até 20 itens com type,
    severity, description e pos; erros primeiro), `unconnected_items`,
    `schematic_parity`, `coordinate_units` e `report_path`, o caminho do JSON
    completo do KiCad. Sem `report_path` o JSON vai para um diretório temporário,
    nunca ao lado do projeto; ele fica lá depois do DRC (para você abrir o
    relatório completo) e só é removido se o DRC falhar. Um `report_path` seu
    precisa terminar em `.json` e não pode ser o próprio `.kicad_pcb`.
    """
    return _guard(
        kicad_cli.pcb_drc, pcb_path, all_severities=all_severities, report_path=report_path
    )


@mcp.tool()
def sch_erc(
    sch_path: str, all_severities: bool = False, report_path: str | None = None
) -> dict[str, Any]:
    """Roda ERC no .kicad_sch e devolve um resumo do relatório.

    Mesmo formato do pcb_drc, com `sheet` em cada item de `first`: a posição
    (`pos`) é relativa àquela folha. Sem `report_path` o JSON completo vai para um
    diretório temporário que fica lá depois do ERC e só é removido se ele falhar.
    Um `report_path` seu precisa terminar em `.json` e não pode ser o próprio
    `.kicad_sch`.
    """
    return _guard(
        kicad_cli.sch_erc, sch_path, all_severities=all_severities, report_path=report_path
    )


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
def pcb_export_step(pcb_path: str, out_path: str, subst_models: bool = True) -> dict[str, Any]:
    """Exporta o .kicad_pcb em STEP, para modelar o gabinete em CAD mecânico.

    `subst_models=True` troca os modelos 3D VRML (.wrl) pelos STEP de mesmo nome,
    que é o que traz esses componentes para o arquivo; com False eles ficam de fora.
    """
    return _guard(
        lambda: {
            "path": str(kicad_cli.pcb_export_step(pcb_path, out_path, subst_models=subst_models))
        }
    )


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
    um arquivo com esse nome exato. O original é salvo como .kicad_dru.bak na
    primeira vez e esse .bak nunca é sobrescrito: `backup_preexistente: true` no
    retorno avisa que o .bak já existia e o arquivo atual foi trocado sem backup
    novo. Rode pcb_drc depois para ver o resultado.
    """
    return _guard(jlcpcb.apply_rules, pcb_path, profile)


@mcp.tool()
def jlcpcb_show_rules(profile: str = jlcpcb.DEFAULT_PROFILE) -> dict[str, Any]:
    """Conteúdo de um perfil, sem escrever nada. Para inspecionar antes de aplicar."""
    return _guard(lambda: {"profile": profile, "rules": jlcpcb.rules_text(profile)})


@mcp.tool()
def jlcpcb_export_fab_package(pcb_path: str, outdir: str | None = None) -> dict[str, Any]:
    """Gera Gerber + Excellon e empacota no zip de upload da JLCPCB.

    Não inclui BOM/CPL — use jlcpcb_export_bom e jlcpcb_export_cpl. Gerber,
    drill e CPL saem na mesma origem (absoluta). Pode rodar de novo no mesmo
    diretório: o zip é sempre refeito por inteiro e só é trocado se a execução
    terminar bem.
    """
    return _guard(jlcpcb.export_fab_package, pcb_path, outdir)


@mcp.tool()
def jlcpcb_export_bom(sch_path: str, out_csv: str) -> dict[str, Any]:
    """Gera o BOM de SMT assembly da JLCPCB a partir do esquemático.

    Colunas: `Designator,Footprint,Quantity,Value,LCSC Part #`. O código LCSC
    é consolidado a partir dos vários nomes de campo que bibliotecas
    diferentes usam (LCSC, LCSC Part #, LCSC#, JLCPCB Part #) — a primeira
    não-vazia vence. Só agrupa componentes com o mesmo Value, Footprint e LCSC;
    os que não têm código compartilham uma linha própria. `missing_lcsc` no
    retorno diz quantas linhas ficaram sem código (linhas, não componentes): a
    JLCPCB rejeita SMT assembly sem ele.
    """
    return _guard(jlcpcb.export_bom, sch_path, out_csv)


@mcp.tool()
def jlcpcb_export_cpl(pcb_path: str, out_csv: str) -> dict[str, Any]:
    """Gera o CPL (posições de montagem) da JLCPCB a partir do PCB.

    Colunas: `Designator,Mid X,Mid Y,Rotation,Layer`, em origem absoluta (a mesma
    dos Gerbers do pacote). Aplica a correção de rotação por encapsulamento
    (SOT-23, DFN, QFN...) usando a tabela baixada por jlcpcb_fetch_rotations. Se a
    tabela não estiver em cache, o CPL sai SEM correção nenhuma e o retorno vem com
    `warning` explícito — rode jlcpcb_fetch_rotations antes de mandar para a fábrica.
    `corrections_applied` no retorno é o antes/depois por designator, para auditoria.
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


# --- Validação por estágios (ADR 0002) -------------------------------------


@mcp.tool()
def validate(
    project: str,
    profile: str = "dev",
    gate: str = "stage",
    until: str | None = None,
    only: list[str] | None = None,
    skip: list[str] | None = None,
    out: str | None = None,
) -> dict[str, Any]:
    """Valida o projeto KiCad por estágios e devolve um veredito único.

    `project` é o .kicad_pro. Estágios: S0 intake, S1 esquema (ERC), S2 placa (DRC e
    paridade), S3 fabricação (DRC com o perfil JLCPCB, numa cópia), S4 saída (Gerber e
    drill), S5 revisão (plugins locais opcionais). Roda sobre uma cópia temporária: o
    projeto nunca é alterado (só `.mcp-kicad/validate/` recebe as saídas).

    `profile`: `dev` reprova em P0/P1 com confiança >= média; `release` em P0-P2.
    `gate`: `stage` termina o estágio que reprovou e para; `full` roda tudo que não
    depende de um check reprovado; `strict` para na primeira reprovação.
    `until`/`only`/`skip` aceitam ids de check ou estágios (`only` inclui os pré-requisitos).
    `out` troca a pasta base e precisa ficar dentro do diretório do projeto.

    Retorno: `verdict` (PASS/FAIL/INCOMPLETE), `run_dir`, `report` (report.json),
    `counts` por severidade, `checks` (status por id), `next_actions` (até 15, só do
    primeiro estágio reprovado, pior primeiro) e `rerun`.
    """
    return _guard(
        lambda: run_validate(
            project,
            profile=profile,
            gate=gate,
            until=until,
            only=only,
            skip=skip,
            out=out,
            restrict_out=True,
        ).summary()
    )


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
