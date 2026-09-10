"""Perfil de fabricação JLCPCB.

Resolve os `.kicad_dru` de rules/jlcpcb/, aplica no projeto e monta o pacote
de fabricação. Números e tabela de sobretaxa: rules/jlcpcb/README.md.
"""

from __future__ import annotations

import csv
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import kicad_cli
from ..errors import (
    InvalidPath,
    ProfileNotFound,
    RotationTableInvalid,
    RotationTableUnavailable,
    UnexpectedCliOutput,
)
from . import rotations


@dataclass(frozen=True, slots=True)
class Profile:
    slug: str
    filename: str
    description: str


PROFILES: dict[str, Profile] = {
    "2layer": Profile(
        "2layer",
        "jlcpcb-2layer-standard.kicad_dru",
        "2 camadas, 1 oz, dentro do envelope sem sobretaxa. Padrão para quase tudo.",
    ),
    "4layer": Profile(
        "4layer",
        "jlcpcb-4layer-standard.kicad_dru",
        "4 camadas: internas em 0.09 mm, externas em 0.127 mm. Sem sobretaxa.",
    ),
    "absoluto": Profile(
        "absoluto",
        "jlcpcb-limites-absolutos.kicad_dru",
        "Limites físicos da fábrica. COM SOBRETAXA — só quando não couber nos outros.",
    ),
}

DEFAULT_PROFILE = "2layer"


def _rules_dir() -> Path:
    """Diretório das regras, funcionando instalado ou rodando do repo."""
    packaged = Path(__file__).parent.parent / "_rules" / "jlcpcb"
    if packaged.is_dir():
        return packaged
    # Rodando direto do checkout: src/mcp_kicad/fab/ -> ../../../rules/jlcpcb
    from_repo = Path(__file__).parents[3] / "rules" / "jlcpcb"
    if from_repo.is_dir():
        return from_repo
    raise ProfileNotFound(
        "diretório de regras não encontrado (nem empacotado, nem no repo)"
    )


def list_profiles() -> list[dict[str, str]]:
    return [
        {"slug": p.slug, "description": p.description, "default": str(p.slug == DEFAULT_PROFILE)}
        for p in PROFILES.values()
    ]


def rules_text(profile: str = DEFAULT_PROFILE) -> str:
    if profile not in PROFILES:
        raise ProfileNotFound(
            f"perfil '{profile}' desconhecido. Disponíveis: {', '.join(PROFILES)}"
        )
    path = _rules_dir() / PROFILES[profile].filename
    if not path.is_file():
        raise ProfileNotFound(f"arquivo de regras ausente: {path}")
    return path.read_text(encoding="utf-8")


def apply_rules(pcb: str | Path, profile: str = DEFAULT_PROFILE) -> Path:
    """Escreve o perfil como `<projeto>.kicad_dru` ao lado do `.kicad_pcb`.

    O KiCad lê custom rules de um único arquivo por projeto, com esse nome
    exato — não há como linkar. Portanto isto **sobrescreve** regras customizadas
    que já existam no projeto. Um `.kicad_dru.bak` é deixado quando havia
    arquivo anterior.
    """
    board = Path(pcb).expanduser().resolve()
    if not board.is_file() or board.suffix != ".kicad_pcb":
        raise InvalidPath(f".kicad_pcb não encontrado: {board}")

    target = board.with_suffix(".kicad_dru")
    if target.is_file():
        shutil.copy2(target, target.with_suffix(".kicad_dru.bak"))

    target.write_text(rules_text(profile), encoding="utf-8")
    return target


# Camadas não-cobre que a fábrica precisa. Paste fica fora: serve para stencil
# de montagem, não para fabricação da placa, e a JLC gera a partir do CPL.
NON_COPPER_LAYERS = [
    "F.Mask",
    "B.Mask",
    "F.Silkscreen",
    "B.Silkscreen",
    "Edge.Cuts",
]


def fab_layers(pcb: str | Path) -> list[str]:
    """Lista de camadas que vão para a JLCPCB, derivada do stackup do board.

    Cobre (quantas houver) + máscara + silk + contorno. Sem Fab, Courtyard ou
    User.* — a fábrica não usa e o F.Fab de um board denso passa de 300 KB.
    """
    return [*kicad_cli.copper_layers(pcb), *NON_COPPER_LAYERS]


def export_fab_package(
    pcb: str | Path,
    outdir: str | Path | None = None,
    *,
    zip_name: str = "gerbers.zip",
) -> dict[str, str]:
    """Gera Gerber + Excellon e empacota no zip que a JLCPCB aceita no upload.

    Não gera BOM/CPL: isso exige o esquemático e a resolução de códigos LCSC,
    que é responsabilidade separada (ver README, seção Roadmap).
    """
    board = Path(pcb).expanduser().resolve()
    if not board.is_file() or board.suffix != ".kicad_pcb":
        raise InvalidPath(f".kicad_pcb não encontrado: {board}")

    dest = Path(outdir).expanduser().resolve() if outdir else board.parent / "production"
    dest.mkdir(parents=True, exist_ok=True)

    layers = fab_layers(board)

    # Empacota pelo que ESTA execução criou, não por extensão. Filtrar por
    # sufixo silenciosamente descarta cobre/máscara/silk quando a extensão
    # Protel está ativa — e o zip resultante parece válido.
    before = {p.name for p in dest.iterdir() if p.is_file()}
    kicad_cli.pcb_export_gerbers(board, dest, layers=layers, protel_ext=False)
    kicad_cli.pcb_export_drill(board, dest)
    produced = sorted(
        p
        for p in dest.iterdir()
        if p.is_file() and p.name not in before and p.name != zip_name
    )

    if not produced:
        raise InvalidPath(f"nenhum Gerber/drill gerado em {dest}")

    missing = [
        layer
        for layer in layers
        # O nome do arquivo usa o apelido do usuário quando existe, então a
        # checagem é por camada canônica achatada (F.Cu -> F_Cu).
        if not any(layer.replace(".", "_") in p.name for p in produced)
    ]

    archive = dest / zip_name
    # A JLCPCB quer os arquivos na raiz do zip, sem diretório intermediário.
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in produced:
            zf.write(member, arcname=member.name)

    result = {
        "zip": str(archive),
        "directory": str(dest),
        "layers_requested": ", ".join(layers),
        "files": str(len(produced)),
    }
    if missing:
        # Camada renomeada no board sai com o apelido no nome do arquivo, então
        # isto é aviso, não erro: confira antes de enviar para a fábrica.
        result["warning"] = (
            "camadas sem arquivo correspondente pelo nome canônico: "
            + ", ".join(missing)
            + ". Podem ter saído com apelido do board — verifique a lista."
        )
    return result


# --- BOM / CPL ---------------------------------------------------------------

# Nomes de campo LCSC que aparecem em bibliotecas diferentes. Consolidados na
# primeira não-vazia, nesta ordem, numa única coluna "LCSC Part #" do CSV
# final — a JLCPCB só entende uma coluna de código.
LCSC_FIELDS = ["LCSC", "LCSC Part #", "LCSC#", "JLCPCB Part #"]


def _raw_sibling(dest: Path) -> Path:
    """Caminho temporário para a saída crua do kicad-cli, ao lado do CSV final."""
    return dest.with_name(dest.stem + ".raw" + dest.suffix)


def _consolidate_lcsc(row: dict[str, str]) -> str:
    """Primeira coluna não-vazia entre LCSC_FIELDS, nessa ordem."""
    for field in LCSC_FIELDS:
        value = (row.get(field) or "").strip()
        if value:
            return value
    return ""


_BOM_REQUIRED_COLUMNS = ["Reference", "Footprint", "QUANTITY", "Value"]
_CPL_REQUIRED_COLUMNS = ["Ref", "Package", "Side", "Rot", "PosX", "PosY"]


def _require_columns(fieldnames: list[str] | None, required: list[str], *, source: str) -> None:
    """Confere que o CSV do kicad-cli tem as colunas que o parsing espera.

    Uma versão futura do kicad-cli renomeando/removendo coluna vira erro
    MK-FAB-004 explícito aqui — não KeyError cru vazando do csv.DictReader
    pro cliente MCP.
    """
    present = set(fieldnames or [])
    missing = [col for col in required if col not in present]
    if missing:
        raise UnexpectedCliOutput(
            f"{source}: coluna(s) esperada(s) ausente(s) na saída do kicad-cli: "
            f"{', '.join(missing)}. Colunas recebidas: {sorted(present)}."
        )


def _parse_float(value: str, *, designator: str, column: str, source: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise UnexpectedCliOutput(
            f"{source}: {designator}, coluna '{column}' não é numérica "
            f"({value!r}) na saída do kicad-cli."
        ) from exc


def export_bom(sch: str | Path, out_csv: str | Path) -> dict[str, Any]:
    """Gera BOM no layout que a JLCPCB aceita no upload de SMT assembly.

    Colunas de saída: `Designator,Footprint,Quantity,Value,LCSC Part #`. O
    código LCSC é consolidado a partir de LCSC_FIELDS — bibliotecas diferentes
    usam nomes de campo diferentes para o mesmo dado, e a JLCPCB só entende
    uma coluna. Linhas sem nenhum desses campos preenchido saem com "LCSC
    Part #" vazio; a contagem vai em `missing_lcsc` no retorno.
    """
    schematic = Path(sch).expanduser().resolve()
    if not schematic.is_file() or schematic.suffix != ".kicad_sch":
        raise InvalidPath(f".kicad_sch não encontrado: {schematic}")

    dest = Path(out_csv).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = _raw_sibling(dest)

    fields = ["Reference", "Footprint", "QUANTITY", "Value", *LCSC_FIELDS]
    kicad_cli.sch_export_bom_fields(
        schematic,
        raw,
        fields=",".join(fields),
        group_by="Value,Footprint",
        exclude_dnp=True,
        ref_range_delimiter="",
    )

    try:
        with raw.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            _require_columns(reader.fieldnames, _BOM_REQUIRED_COLUMNS, source="jlcpcb_export_bom")
            rows = list(reader)
    finally:
        raw.unlink(missing_ok=True)

    missing_lcsc = 0
    out_rows: list[dict[str, str]] = []
    for row in rows:
        lcsc = _consolidate_lcsc(row)
        if not lcsc:
            missing_lcsc += 1
        out_rows.append(
            {
                "Designator": row.get("Reference", ""),
                "Footprint": row.get("Footprint", ""),
                "Quantity": row.get("QUANTITY", ""),
                "Value": row.get("Value", ""),
                "LCSC Part #": lcsc,
            }
        )

    header = ["Designator", "Footprint", "Quantity", "Value", "LCSC Part #"]
    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(out_rows)

    result: dict[str, Any] = {
        "path": str(dest),
        "rows": len(out_rows),
        "missing_lcsc": missing_lcsc,
    }
    if missing_lcsc:
        result["warning"] = (
            f"{missing_lcsc} linha(s) sem código LCSC — a JLCPCB exige LCSC "
            "Part # para SMT assembly."
        )
    return result


def export_cpl(pcb: str | Path, out_csv: str | Path) -> dict[str, Any]:
    """Gera CPL (posições de montagem) com a correção de rotação da JLCPCB aplicada.

    Colunas de saída: `Designator,Mid X,Mid Y,Rotation,Layer`. A correção de
    rotação depende da tabela baixada por rotations.fetch_table() (tool
    jlcpcb_fetch_rotations). Se ela não estiver em cache, o CPL ainda é
    gerado — sem NENHUMA correção — mas o retorno vem com `warning` explícito:
    silêncio aqui vira placa montada com componentes girados errado.

    A tabela também traz offset X/Y para algumas linhas (ex:
    `USB_C_Receptacle_HRO...`, `PinHeader_2x05...`). **O offset NÃO é
    aplicado** — só a rotação. Motivo: o offset da tabela foi calibrado pelo
    `kicad-jlcpcb-tools` sobre o centro da bounding box dos pads, e este
    módulo usa a origem do footprint (o que o `kicad-cli` entrega, ver
    docs/rotacoes-cpl.md) — não há confirmação de que os dois valores usam a
    mesma convenção, e deslocar um componente com base em palpite é pior que
    não deslocar. Quando um footprint casa com uma correção que tem offset,
    o designator entra em `corrections_applied` com `offset_pendente`
    preenchido, e o retorno vem com `warning` nomeando quem precisa de
    conferência manual de posição antes da montagem.

    `corrections_applied` no retorno lista, por designator, a rotação crua do
    kicad-cli e a rotação final gravada no CSV — é o antes/depois para
    auditoria antes de enviar para a fábrica.
    """
    board = Path(pcb).expanduser().resolve()
    if not board.is_file() or board.suffix != ".kicad_pcb":
        raise InvalidPath(f".kicad_pcb não encontrado: {board}")

    dest = Path(out_csv).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = _raw_sibling(dest)

    kicad_cli.pcb_export_pos(board, raw, use_drill_file_origin=True, exclude_dnp=True)

    table: list[rotations.Correction] = []
    warnings: list[str] = []
    try:
        table = rotations.load_table()
    except (RotationTableUnavailable, RotationTableInvalid) as exc:
        warnings.append(
            "nenhuma correção de rotação aplicada — tabela indisponível "
            f"({exc}). Rode jlcpcb_fetch_rotations e gere o CPL de novo antes "
            "de enviar para montagem."
        )

    try:
        with raw.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            _require_columns(reader.fieldnames, _CPL_REQUIRED_COLUMNS, source="jlcpcb_export_cpl")
            rows = list(reader)
    finally:
        raw.unlink(missing_ok=True)

    applied: list[dict[str, Any]] = []
    offset_pending: list[str] = []
    out_rows: list[dict[str, str]] = []
    for row in rows:
        designator = row["Ref"]
        # Package no pos file já vem sem prefixo de lib, diferente do
        # Footprint do BOM — corta no ":" mesmo assim, por segurança.
        package = row["Package"].split(":", 1)[-1]
        side = row["Side"].strip().lower()
        rot_original = _parse_float(
            row["Rot"], designator=designator, column="Rot", source="jlcpcb_export_cpl"
        )
        pos_x = _parse_float(
            row["PosX"], designator=designator, column="PosX", source="jlcpcb_export_cpl"
        )
        pos_y = _parse_float(
            row["PosY"], designator=designator, column="PosY", source="jlcpcb_export_cpl"
        )

        correction = rotations.find_correction(package, table) if table else None
        rot_corrected = rotations.corrected_rotation(rot_original, side, correction)

        if correction is not None:
            entry: dict[str, Any] = {
                "designator": designator,
                "package": package,
                "rotation_original": rot_original,
                "rotation_corrigida": rot_corrected,
            }
            if correction.offset_x != 0 or correction.offset_y != 0:
                entry["offset_pendente"] = {
                    "offset_x": correction.offset_x,
                    "offset_y": correction.offset_y,
                }
                offset_pending.append(designator)
            applied.append(entry)

        out_rows.append(
            {
                "Designator": designator,
                "Mid X": f"{pos_x:.4f}",
                "Mid Y": f"{pos_y:.4f}",
                "Rotation": f"{rot_corrected:.2f}",
                "Layer": side,
            }
        )

    if offset_pending:
        warnings.append(
            "offset de posição da tabela NÃO foi aplicado para "
            + ", ".join(offset_pending)
            + " — só a rotação foi corrigida. Confira Mid X/Mid Y manualmente "
            "antes de mandar para montagem (ver docs/rotacoes-cpl.md)."
        )

    header = ["Designator", "Mid X", "Mid Y", "Rotation", "Layer"]
    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(out_rows)

    result: dict[str, Any] = {
        "path": str(dest),
        "rows": len(out_rows),
        "corrections_applied": applied,
    }
    if warnings:
        result["warning"] = " ".join(warnings)
    return result
