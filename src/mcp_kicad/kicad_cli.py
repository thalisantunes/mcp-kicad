"""Wrapper sobre `kicad-cli`.

Caminho primário do servidor. Funciona headless, sem o KiCad aberto, e cobre
esquemático e todo o export — coisas que a IPC API do KiCad 10 não faz.
Ver docs/adr/0001-ipc-api-mais-kicad-cli.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import InvalidPath, KicadCliFailed, KicadCliNotFound

# Timeout generoso: export de Gerber em placa grande passa de 30s com folga.
DEFAULT_TIMEOUT_S = 300

# Ordem de busca: env explícito, PATH, caminhos do PPA/apt, Flatpak.
_FLATPAK_CMD = ["flatpak", "run", "--command=kicad-cli", "org.kicad.KiCad"]


def resolve_cli() -> list[str]:
    """Devolve o comando base do kicad-cli, como lista para subprocess.

    `MCP_KICAD_CLI` sobrescreve tudo — use quando houver mais de uma instalação
    e você quiser fixar qual delas o MCP usa.
    """
    override = os.environ.get("MCP_KICAD_CLI")
    if override:
        return override.split()

    found = shutil.which("kicad-cli")
    if found:
        return [found]

    if shutil.which("flatpak"):
        probe = subprocess.run(  # noqa: S603
            ["flatpak", "info", "org.kicad.KiCad"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return list(_FLATPAK_CMD)

    raise KicadCliNotFound(
        "kicad-cli não encontrado. Instale o KiCad (ver README) ou aponte "
        "MCP_KICAD_CLI para o binário."
    )


@dataclass(slots=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(args: list[str], *, timeout_s: int = DEFAULT_TIMEOUT_S, check: bool = True) -> CliResult:
    """Roda `kicad-cli <args>`.

    `check=False` é necessário para DRC/ERC: com --exit-code-violations o
    returncode 5 significa "achou violação", que é resultado válido, não falha.
    """
    cmd = [*resolve_cli(), *args]
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise KicadCliFailed(
            f"kicad-cli excedeu {timeout_s}s em: {' '.join(args)}",
            returncode=-1,
        ) from exc

    result = CliResult(proc.returncode, proc.stdout, proc.stderr)
    if check and not result.ok:
        raise KicadCliFailed(
            f"kicad-cli falhou ({proc.returncode}) em: {' '.join(args)}",
            returncode=proc.returncode,
            stderr=result.stderr.strip(),
        )
    return result


def version() -> str:
    return run(["version"]).stdout.strip()


def _require(path: str | Path, suffix: str) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise InvalidPath(f"arquivo não encontrado: {p}")
    if p.suffix != suffix:
        raise InvalidPath(f"esperado {suffix}, recebido {p.suffix}: {p}")
    return p


# --- Verificação -----------------------------------------------------------


def pcb_drc(pcb: str | Path, *, all_severities: bool = False) -> dict[str, Any]:
    """DRC no PCB, saída JSON parseada.

    Retorna o relatório do KiCad como dict. Chave prática: `violations`.
    """
    board = _require(pcb, ".kicad_pcb")
    out = board.with_suffix(".drc.json")
    args = [
        "pcb",
        "drc",
        "--format",
        "json",
        "--output",
        str(out),
        "--exit-code-violations",
    ]
    args.append("--severity-all" if all_severities else "--severity-error")
    args.append(str(board))

    result = run(args, check=False)
    # 0 = limpo, 5 = violações encontradas. Qualquer outro é falha real.
    if result.returncode not in (0, 5):
        raise KicadCliFailed(
            f"DRC falhou em {board.name}",
            returncode=result.returncode,
            stderr=result.stderr.strip(),
        )
    return _read_report(out, kind="DRC")


def sch_erc(sch: str | Path, *, all_severities: bool = False) -> dict[str, Any]:
    """ERC no esquemático, saída JSON parseada."""
    schematic = _require(sch, ".kicad_sch")
    out = schematic.with_suffix(".erc.json")
    args = [
        "sch",
        "erc",
        "--format",
        "json",
        "--output",
        str(out),
        "--exit-code-violations",
    ]
    args.append("--severity-all" if all_severities else "--severity-error")
    args.append(str(schematic))

    result = run(args, check=False)
    if result.returncode not in (0, 5):
        raise KicadCliFailed(
            f"ERC falhou em {schematic.name}",
            returncode=result.returncode,
            stderr=result.stderr.strip(),
        )
    return _read_report(out, kind="ERC")


def _read_report(path: Path, *, kind: str) -> dict[str, Any]:
    if not path.is_file():
        raise KicadCliFailed(
            f"{kind} não gerou relatório em {path}", returncode=0
        )
    return json.loads(path.read_text(encoding="utf-8"))


# --- Export ----------------------------------------------------------------


def copper_layers(pcb: str | Path) -> list[str]:
    """Nomes CANÔNICOS das camadas de cobre do board, na ordem do stackup.

    Lê direto o `.kicad_pcb`. O bloco `(layers ...)` guarda o nome canônico
    (`"F.Cu"`) e, opcionalmente, um apelido do usuário como 4º token
    (`(0 "F.Cu" power "top_copper")`). O `--layers` do kicad-cli espera o
    canônico, então o apelido é ignorado aqui de propósito — só o nome do
    arquivo de saída usa o apelido.
    """
    board = _require(pcb, ".kicad_pcb")
    text = board.read_text(encoding="utf-8", errors="replace")
    found: list[tuple[int, str]] = []
    for match in re.finditer(r'\(\s*(\d+)\s+"([^"]+\.Cu)"', text):
        found.append((int(match.group(1)), match.group(2)))
    if not found:
        raise KicadCliFailed(
            f"nenhuma camada de cobre encontrada em {board.name}", returncode=0
        )
    return [name for _, name in sorted(set(found))]


def pcb_export_gerbers(
    pcb: str | Path,
    outdir: str | Path,
    *,
    layers: list[str] | None = None,
    protel_ext: bool = True,
) -> Path:
    """Exporta Gerbers.

    `layers` usa nomes canônicos (F.Cu, B.Cu, F.Mask, Edge.Cuts...). Sem essa
    lista o KiCad plota tudo que estiver habilitado no board, o que inclui
    Fab/Courtyard/User — camadas que não vão para a fábrica.

    `protel_ext=False` força extensão `.gbr` em tudo. Com o default do KiCad
    (Protel), cada camada sai com extensão própria — `.gtl`, `.gbl`, `.gts`,
    `.gto`, `.gm1` — e só as camadas User/Fab ficam com `.gbr`. Filtrar a
    saída por extensão é, por isso, uma armadilha.
    """
    board = _require(pcb, ".kicad_pcb")
    dest = Path(outdir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    args = ["pcb", "export", "gerbers", "--output", str(dest)]
    if layers:
        args += ["--layers", ",".join(layers)]
    if not protel_ext:
        args.append("--no-protel-ext")
    args.append(str(board))
    run(args)
    return dest


def pcb_export_drill(pcb: str | Path, outdir: str | Path) -> Path:
    board = _require(pcb, ".kicad_pcb")
    dest = Path(outdir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    run(["pcb", "export", "drill", "--output", str(dest), str(board)])
    return dest


def sch_export_bom(sch: str | Path, out_csv: str | Path) -> Path:
    schematic = _require(sch, ".kicad_sch")
    dest = Path(out_csv).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["sch", "export", "bom", "--output", str(dest), str(schematic)])
    return dest


def sch_export_netlist(sch: str | Path, out_net: str | Path) -> Path:
    schematic = _require(sch, ".kicad_sch")
    dest = Path(out_net).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["sch", "export", "netlist", "--output", str(dest), str(schematic)])
    return dest


def sch_export_bom_fields(
    sch: str | Path,
    out_csv: str | Path,
    *,
    fields: str,
    group_by: str | None = None,
    exclude_dnp: bool = True,
    ref_range_delimiter: str = "",
) -> Path:
    """Exporta BOM com colunas e agrupamento customizados (uso: fab/jlcpcb.py).

    `--fields` aceita nomes de campo inexistentes: saem como coluna vazia, sem
    erro do kicad-cli (verificado no KiCad 10.0.5). `--ref-range-delimiter ""`
    desliga a compressão de faixa de referências (R1-R3 vira R1,R2,R3).
    `--include-excluded-from-bom` não é usado aqui: está deprecado e não faz
    nada — componentes "Exclude from bill of materials" já saem sempre
    excluídos do BOM.
    """
    schematic = _require(sch, ".kicad_sch")
    dest = Path(out_csv).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["sch", "export", "bom", "--output", str(dest), "--fields", fields]
    if group_by:
        args += ["--group-by", group_by]
    if exclude_dnp:
        args.append("--exclude-dnp")
    args += ["--ref-range-delimiter", ref_range_delimiter]
    args.append(str(schematic))
    run(args)
    return dest


def pcb_export_pos(
    pcb: str | Path,
    out_csv: str | Path,
    *,
    use_drill_file_origin: bool = True,
    exclude_dnp: bool = True,
) -> Path:
    """Exporta posições de componentes (placement) em CSV (uso: fab/jlcpcb.py).

    Saída: `Ref,Val,Package,PosX,PosY,Rot,Side`. Com `--use-drill-file-origin`
    o kicad-cli já subtrai a aux origin e já inverte o eixo Y — não reaplique
    nenhuma transformação de Y por cima disso. Ele NÃO espelha o ângulo do
    bottom: `Rot` sai cru do footprint, como gravado no `.kicad_pcb`. A
    correção de rotação por encapsulamento é responsabilidade de
    fab/rotations.py. Ver docs/rotacoes-cpl.md.
    """
    board = _require(pcb, ".kicad_pcb")
    dest = Path(out_csv).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["pcb", "export", "pos", "--format", "csv", "--units", "mm", "--output", str(dest)]
    if use_drill_file_origin:
        args.append("--use-drill-file-origin")
    if exclude_dnp:
        args.append("--exclude-dnp")
    args.append(str(board))
    run(args)
    return dest
