"""Wrapper sobre `kicad-cli`.

Caminho primário do servidor. Funciona headless, sem o KiCad aberto, e cobre
esquemático e todo o export — coisas que a IPC API do KiCad 10 não faz.
Ver docs/adr/0001-ipc-api-mais-kicad-cli.md.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import InvalidPath, KicadCliFailed, KicadCliNotFound

# Timeout generoso: export de Gerber em placa grande passa de 30s com folga.
DEFAULT_TIMEOUT_S = 300

# Ordem de busca: MCP_KICAD_CLI, PATH, instalações conhecidas, Flatpak. O DMG do
# macOS não põe o kicad-cli no PATH (o cask do Homebrew cria um symlink).
_KNOWN_CLI = [
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "~/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
]
_FLATPAK_CMD = ["flatpak", "run", "--command=kicad-cli", "org.kicad.KiCad"]
_FLATPAK_PROBE_TIMEOUT_S = 10


def _split_command(value: str, *, posix: bool) -> list[str]:
    """Quebra um comando em argumentos respeitando aspas.

    Fora do modo POSIX (Windows) as barras invertidas ficam intactas; o shlex
    nesse modo mantém as aspas no token, então elas são removidas aqui.
    """
    return [token.strip('"') for token in shlex.split(value, posix=posix)]


def resolve_cli() -> list[str]:
    """Devolve o comando base do kicad-cli, como lista para subprocess.

    `MCP_KICAD_CLI` sobrescreve tudo — use quando houver mais de uma instalação
    e você quiser fixar qual delas o MCP usa. Aceita um caminho (mesmo com espaço,
    sem aspas) ou um comando com argumentos (ex.: o do Flatpak).
    """
    override = os.environ.get("MCP_KICAD_CLI")
    if override:
        single = Path(override).expanduser()
        if single.is_file():
            return [str(single)]
        try:
            return _split_command(override, posix=os.name != "nt")
        except ValueError as exc:  # aspas desbalanceadas
            raise KicadCliNotFound(
                f"MCP_KICAD_CLI inválida ({exc}): confira as aspas em {override!r}."
            ) from exc

    found = shutil.which("kicad-cli")
    if found:
        return [found]

    for candidate in _KNOWN_CLI:
        path = Path(candidate).expanduser()
        if path.is_file():
            return [str(path)]

    if shutil.which("flatpak"):
        try:
            probe = subprocess.run(  # noqa: S603
                ["flatpak", "info", "org.kicad.KiCad"],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                encoding="utf-8",
                errors="replace",
                timeout=_FLATPAK_PROBE_TIMEOUT_S,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            probe = None  # flatpak travado ou inexecutável: tratar como não instalado
        if probe is not None and probe.returncode == 0:
            return list(_FLATPAK_CMD)

    searched = ["MCP_KICAD_CLI (não definida)", "PATH", *_KNOWN_CLI, "Flatpak (org.kicad.KiCad)"]
    raise KicadCliNotFound(
        f"kicad-cli não encontrado. Procurei em: {', '.join(searched)}. Instale o KiCad "
        "(ver README) ou aponte MCP_KICAD_CLI para o binário, por exemplo: "
        "MCP_KICAD_CLI=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
    )


@dataclass(slots=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


_STDERR_TAIL_LINES = 15


def _failure(message: str, result: CliResult) -> KicadCliFailed:
    """KicadCliFailed com o fim do stderr na mensagem: é o motivo que o agente precisa ver."""
    tail = "\n".join(result.stderr.strip().splitlines()[-_STDERR_TAIL_LINES:])
    return KicadCliFailed(
        f"{message}\n{tail}" if tail else message,
        returncode=result.returncode,
        stderr=result.stderr.strip(),
    )


def run(args: list[str], *, timeout_s: int = DEFAULT_TIMEOUT_S, check: bool = True) -> CliResult:
    """Roda `kicad-cli <args>`.

    `check=False` é necessário para DRC/ERC: com --exit-code-violations o
    returncode 5 significa "achou violação", que é resultado válido, não falha.

    O stdin do servidor é o canal JSON-RPC do MCP, então o filho nunca o herda.
    """
    cmd = [*resolve_cli(), *args]
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise KicadCliFailed(
            f"kicad-cli excedeu {timeout_s}s em: {' '.join(args)}",
            returncode=-1,
        ) from exc
    except OSError as exc:
        raise KicadCliNotFound(
            f"não foi possível executar {cmd[0]!r}: {exc}. Confira MCP_KICAD_CLI ou "
            "instale o KiCad (ver README)."
        ) from exc

    result = CliResult(proc.returncode, proc.stdout, proc.stderr)
    if check and not result.ok:
        raise _failure(f"kicad-cli falhou ({proc.returncode}) em: {' '.join(args)}", result)
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


def pcb_drc(
    pcb: str | Path,
    *,
    all_severities: bool = False,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """DRC no PCB. Devolve um resumo; o relatório JSON completo fica em `report_path`.

    Sem `report_path` o relatório vai para um diretório temporário, nunca ao lado
    do projeto (que pode ser somente leitura). Esse diretório NÃO é apagado quando
    o DRC termina: o resumo devolve o caminho para abrir o relatório completo, e o
    sistema limpa o temporário depois. Só uma falha o remove. Um `report_path`
    precisa terminar em `.json` e não pode ser o próprio `.kicad_pcb`. Ver
    `_summarize` para as chaves do resumo.
    """
    board = _require(pcb, ".kicad_pcb")
    with _report_file(report_path, f"{board.stem}.drc.json", board) as out:
        report = drc_report(board, out, parity=False, all_severities=all_severities)
        return _summarize(report, out)


def sch_erc(
    sch: str | Path,
    *,
    all_severities: bool = False,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """ERC no esquemático. Mesmo formato e mesmas regras de `report_path` do pcb_drc.

    Cada item de `first` traz a folha (`sheet`) em que a violação está.
    """
    schematic = _require(sch, ".kicad_sch")
    with _report_file(report_path, f"{schematic.stem}.erc.json", schematic) as out:
        report = erc_report(schematic, out, all_severities=all_severities)
        return _summarize(report, out)


def drc_report(
    pcb: Path, out: Path, parity: bool = True, all_severities: bool = True
) -> dict[str, Any]:
    """Roda o DRC e devolve o relatório JSON cru (gravado em `out`).

    Sem validar o caminho de saída: quem chama escolhe `out`. `parity` liga a
    comparação com o esquemático (`--schematic-parity`).
    """
    args = ["pcb", "drc", "--format", "json", "--output", str(out), "--exit-code-violations"]
    args.append("--severity-all" if all_severities else "--severity-error")
    if parity:
        args.append("--schematic-parity")
    args.append(str(pcb))
    result = run(args, check=False)
    # 0 = limpo, 5 = violações encontradas. Qualquer outro é falha real.
    if result.returncode not in (0, 5):
        raise _failure(f"DRC falhou ({result.returncode}) em {pcb.name}", result)
    return _read_report(out, kind="DRC")


def erc_report(sch: Path, out: Path, all_severities: bool = True) -> dict[str, Any]:
    """Roda o ERC e devolve o relatório JSON cru (gravado em `out`)."""
    args = ["sch", "erc", "--format", "json", "--output", str(out), "--exit-code-violations"]
    args.append("--severity-all" if all_severities else "--severity-error")
    args.append(str(sch))
    result = run(args, check=False)
    if result.returncode not in (0, 5):
        raise _failure(f"ERC falhou ({result.returncode}) em {sch.name}", result)
    return _read_report(out, kind="ERC")


@contextmanager
def _report_file(report_path: str | Path | None, name: str, source: Path) -> Iterator[Path]:
    """Caminho do relatório JSON de DRC/ERC.

    Com `report_path`, é o caminho do chamador: precisa terminar em `.json` e não
    pode ser o próprio arquivo de entrada (o kicad-cli o sobrescreveria com o
    relatório). Sem ele, é um arquivo num diretório temporário novo, que FICA em
    caso de sucesso (o resumo devolve o caminho) e só é apagado se o bloco falhar.
    """
    if report_path:
        given = Path(report_path).expanduser()
        if given.suffix != ".json":
            raise InvalidPath(f"report_path deve terminar em .json: {given}")
        target = given.resolve()
        if target == source:  # ex.: um symlink .json apontando para a placa
            raise InvalidPath(f"report_path não pode ser o arquivo de entrada: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        yield target
        return

    scratch = Path(tempfile.mkdtemp(prefix="mcp-kicad-"))
    try:
        yield scratch / name
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise


def _read_report(path: Path, *, kind: str) -> dict[str, Any]:
    if not path.is_file():
        raise KicadCliFailed(
            f"{kind} não gerou relatório em {path}", returncode=0
        )
    return json.loads(path.read_text(encoding="utf-8"))


_FIRST_VIOLATIONS = 20
_SEVERITY_RANK = {"error": 0, "warning": 1}  # o resto (exclusões...) vem depois


def _summarize(report: dict[str, Any], path: Path) -> dict[str, Any]:
    """Resume o relatório de DRC (`violations`) ou de ERC (`sheets[].violations`).

    O relatório de uma placa real passa fácil de 100 KiB (296 KiB com 507
    violações), grande demais para uma resposta de tool. Chaves: `violations`
    (total), `by_severity`, `first` (até 20 itens, erros antes de warnings; `pos`
    é a do primeiro item envolvido), `unconnected_items` e `schematic_parity` (só
    no DRC), `coordinate_units` e `report_path` (o relatório completo).
    """
    found = [(None, v) for v in report.get("violations", [])]
    found += [(s.get("path"), v) for s in report.get("sheets", []) for v in s.get("violations", [])]
    found.sort(key=lambda item: _SEVERITY_RANK.get(item[1].get("severity"), 2))

    first = []
    for sheet, violation in found[:_FIRST_VIOLATIONS]:
        entry = {
            "type": violation.get("type"),
            "severity": violation.get("severity"),
            "description": violation.get("description"),
            "pos": next((i["pos"] for i in violation.get("items", []) if "pos" in i), None),
        }
        if sheet is not None:
            entry["sheet"] = sheet
        first.append(entry)

    summary: dict[str, Any] = {
        "violations": len(found),
        "by_severity": dict(Counter(v.get("severity") for _, v in found)),
        "first": first,
        "coordinate_units": report.get("coordinate_units"),
        "report_path": str(path),
    }
    for key in ("unconnected_items", "schematic_parity"):
        if key in report:
            summary[key] = len(report[key])
    return summary


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
    exclude_dnp: bool = True,
) -> Path:
    """Exporta posições de componentes (placement) em CSV (uso: fab/jlcpcb.py).

    Saída: `Ref,Val,Package,PosX,PosY,Rot,Side`, em origem ABSOLUTA, a mesma que
    pcb_export_gerbers e pcb_export_drill usam por padrão. Não passe
    `--use-drill-file-origin`: ele moveria só o CPL para a aux origin e o
    desalinharia dos Gerbers do mesmo pacote. O kicad-cli já inverte o eixo Y
    (PosY sai com o sinal do Gerber) — não reaplique nenhuma transformação de Y
    por cima disso. Ele NÃO espelha o ângulo do bottom: `Rot` sai cru do
    footprint, como gravado no `.kicad_pcb`. A correção de rotação por
    encapsulamento é responsabilidade de fab/rotations.py. Ver docs/rotacoes-cpl.md.
    """
    board = _require(pcb, ".kicad_pcb")
    dest = Path(out_csv).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["pcb", "export", "pos", "--format", "csv", "--units", "mm", "--output", str(dest)]
    if exclude_dnp:
        args.append("--exclude-dnp")
    args.append(str(board))
    run(args)
    return dest


def pcb_export_step(
    pcb: str | Path,
    out_path: str | Path,
    *,
    subst_models: bool = True,
) -> Path:
    """Exporta o board em STEP (ponte para CAD mecânico, ex.: gabinete no FreeCAD).

    `subst_models` troca os modelos 3D VRML (.wrl) pelos STEP de mesmo nome, que
    é o que põe esses componentes no arquivo; sem isso eles ficam de fora e o STEP
    sai só com a placa e os componentes cujo modelo já é STEP. Uma saída que já
    existe é sobrescrita (`--force`), como nos outros exports.
    """
    board = _require(pcb, ".kicad_pcb")
    dest = Path(out_path).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["pcb", "export", "step", "--force"]
    if subst_models:
        args.append("--subst-models")
    run([*args, "--output", str(dest), str(board)])
    return dest
