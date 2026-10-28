"""Testes contra o kicad-cli real e os projetos de demo do KiCad.

Marcados `integration`: pulam sozinhos sem kicad-cli (ver conftest.py) e ficam
fora do CI (`-m "not integration"`). Sempre trabalham numa cópia do demo.

- `cm5_minima`: 6 camadas, aux origin em (78.15, 81.35), 2 erros de DRC (KiCad 10.0.5).
- `complex_hierarchy`: 2 camadas, quase tudo through-hole.
"""

from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client

from mcp_kicad import kicad_cli
from mcp_kicad.fab import jlcpcb

pytestmark = pytest.mark.integration

CM5_PCB = "CM5_MINIMA_3.kicad_pcb"
CM5_SCH = "CM5_MINIMA_3.kicad_sch"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _zip_names(path: str) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return sorted(zf.namelist())


def _outline_bbox(gerber: Path) -> tuple[float, float, float, float]:
    """(xmin, xmax, ymin, ymax) em mm do contorno, lido das coordenadas do Gerber."""
    text = gerber.read_text(encoding="utf-8")
    assert "%FSLAX46Y46*%" in text and "%MOMM*%" in text  # formato 4.6 em mm, assumido abaixo
    points = re.findall(r"^X(-?\d+)Y(-?\d+)D0[123]\*", text, re.MULTILINE)
    xs = [int(x) / 1e6 for x, _ in points]
    ys = [int(y) / 1e6 for _, y in points]
    return min(xs), max(xs), min(ys), max(ys)


# --- F1: Gerber, drill e CPL no mesmo quadro de coordenadas ------------------


def test_cpl_positions_fall_inside_the_gerber_outline(demo_project, tmp_path) -> None:
    pcb = demo_project("cm5_minima") / CM5_PCB
    package = jlcpcb.export_fab_package(pcb, tmp_path / "out")
    jlcpcb.export_cpl(pcb, tmp_path / "out" / "cpl.csv")

    xmin, xmax, ymin, ymax = _outline_bbox(
        Path(package["directory"]) / "CM5_MINIMA_3-Edge_Cuts.gbr"
    )
    # o demo tem aux origin longe de zero; sem isso o teste não distinguiria os quadros
    assert xmin > 10

    margin = 2.0  # mm: a origem de um conector pode ficar um pouco além da borda
    rows = _read_csv(tmp_path / "out" / "cpl.csv")
    assert rows
    outside = [
        row["Designator"]
        for row in rows
        if not (xmin - margin <= float(row["Mid X"]) <= xmax + margin)
        or not (ymin - margin <= float(row["Mid Y"]) <= ymax + margin)
    ]
    assert outside == []


# --- F2: o pacote JLC pode ser gerado de novo no mesmo diretório -------------


def test_fab_package_can_be_rerun_in_the_same_directory(demo_project) -> None:
    pcb = demo_project("cm5_minima") / CM5_PCB

    first = jlcpcb.export_fab_package(pcb)  # outdir padrão: <pasta do pcb>/production
    names = _zip_names(first["zip"])
    assert len(names) >= 12

    second = jlcpcb.export_fab_package(pcb)
    assert _zip_names(second["zip"]) == names

    # estado parcial: sumir um arquivo solto não pode virar um zip de 1 arquivo
    (Path(second["directory"]) / "CM5_MINIMA_3-F_Mask.gbr").unlink()
    third = jlcpcb.export_fab_package(pcb)
    assert _zip_names(third["zip"]) == names


# --- F6: BOM não junta códigos LCSC diferentes --------------------------------


def _set_first_lcsc(project: Path, code: str) -> None:
    """Troca o LCSC de um dos capacitores 10u 0805 (C15850) do demo."""
    sheet = project / "PCIe-M2.kicad_sch"
    text = sheet.read_text(encoding="utf-8")
    old = '(property "LCSC" "C15850"'
    assert old in text
    sheet.write_text(text.replace(old, f'(property "LCSC" "{code}"', 1), encoding="utf-8")


def test_bom_does_not_merge_different_lcsc_codes(demo_project, tmp_path) -> None:
    project = demo_project("cm5_minima")
    baseline = jlcpcb.export_bom(project / CM5_SCH, tmp_path / "base.csv")

    _set_first_lcsc(project, "C99999")
    result = jlcpcb.export_bom(project / CM5_SCH, tmp_path / "bom.csv")

    rows = _read_csv(tmp_path / "bom.csv")
    assert [row for row in rows if "," in row["LCSC Part #"]] == []
    changed = [row for row in rows if row["LCSC Part #"] == "C99999"]
    assert len(changed) == 1
    assert changed[0]["Quantity"] == "1"
    assert result["missing_lcsc"] == baseline["missing_lcsc"]


def test_bom_counts_the_row_without_lcsc_as_missing(demo_project, tmp_path) -> None:
    project = demo_project("cm5_minima")
    baseline = jlcpcb.export_bom(project / CM5_SCH, tmp_path / "base.csv")

    _set_first_lcsc(project, "")
    result = jlcpcb.export_bom(project / CM5_SCH, tmp_path / "bom.csv")

    assert result["missing_lcsc"] == baseline["missing_lcsc"] + 1


# --- F7: DRC e ERC devolvem resumo, com o relatório fora do projeto ----------


def test_drc_returns_summary_and_keeps_the_project_clean(demo_project) -> None:
    project = demo_project("cm5_minima")

    summary = kicad_cli.pcb_drc(project / CM5_PCB)

    assert summary["violations"] == 2
    assert summary["by_severity"] == {"error": 2}
    assert len(summary["first"]) == 2
    assert all(v["pos"] is not None for v in summary["first"])
    report = Path(summary["report_path"])
    assert json.loads(report.read_text(encoding="utf-8"))["violations"]  # relatório completo
    assert project not in report.parents
    assert list(project.glob("*.drc.json")) == []


def test_drc_all_severities_finds_more_than_the_errors(demo_project) -> None:
    project = demo_project("cm5_minima")

    summary = kicad_cli.pcb_drc(project / CM5_PCB, all_severities=True)

    assert summary["violations"] > 2
    assert len(summary["first"]) == 20
    assert summary["first"][0]["severity"] == "error"  # erros antes de warnings


def test_erc_returns_summary_with_the_sheet_of_each_violation(demo_project) -> None:
    project = demo_project("cm5_minima")

    summary = kicad_cli.sch_erc(project / CM5_SCH, all_severities=True)

    assert summary["violations"] >= 1
    assert summary["by_severity"].get("warning", 0) >= 1
    assert all("sheet" in v for v in summary["first"])
    assert list(project.glob("*.erc.json")) == []


# --- F11: STEP para o gabinete -----------------------------------------------


def test_step_export_writes_a_real_step_file(demo_project, tmp_path) -> None:
    pcb = demo_project("complex_hierarchy") / "complex_hierarchy.kicad_pcb"

    out = kicad_cli.pcb_export_step(pcb, tmp_path / "step" / "board.step")

    assert out.stat().st_size > 10_000
    assert out.read_bytes().startswith(b"ISO-10303-21")


def test_step_export_overwrites_the_same_path_on_a_second_run(demo_project, tmp_path) -> None:
    pcb = demo_project("complex_hierarchy") / "complex_hierarchy.kicad_pcb"
    out = tmp_path / "board.step"
    kicad_cli.pcb_export_step(pcb, out)
    out.write_bytes(b"STALE")  # prova que a 2ª execução sobrescreve de fato

    kicad_cli.pcb_export_step(pcb, out)

    assert out.read_bytes().startswith(b"ISO-10303-21")
    assert out.stat().st_size > 10_000


# --- Tools pela rede (stdio): argumentos e retornos como o cliente os vê -----


async def test_tools_over_the_wire(demo_project, tmp_path, server_params) -> None:
    project = demo_project("complex_hierarchy")
    pcb = str(project / "complex_hierarchy.kicad_pcb")
    (project / "complex_hierarchy.kicad_dru").write_text("ORIGINAL_USER_RULES", encoding="utf-8")
    step_path = str(tmp_path / "wire.step")

    async with stdio_client(server_params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        step = await session.call_tool("pcb_export_step", {"pcb_path": pcb, "out_path": step_path})
        drc = await session.call_tool("pcb_drc", {"pcb_path": pcb})
        first = await session.call_tool("jlcpcb_apply_rules", {"pcb_path": pcb})
        second = await session.call_tool(
            "jlcpcb_apply_rules", {"pcb_path": pcb, "profile": "4layer"}
        )

    assert step.is_error is False
    assert step.structured_content == {"path": step_path}
    assert Path(step_path).stat().st_size > 10_000

    assert drc.is_error is False
    assert drc.structured_content["violations"] == 0
    assert drc.structured_content["first"] == []

    assert first.structured_content["backup_preexistente"] is False
    assert second.structured_content["backup_preexistente"] is True
    backup = project / "complex_hierarchy.kicad_dru.bak"
    assert backup.read_text(encoding="utf-8") == "ORIGINAL_USER_RULES"


# --- validate (ADR 0002) -----------------------------------------------------


def test_validate_end_to_end_on_kicad_demo(demo_project) -> None:
    from mcp_kicad.validate import run_validate

    project = demo_project("interf_u") / "interf_u.kicad_pro"
    design = [p for p in project.parent.glob("*.kicad_*") if p.suffix != ".kicad_prl"]
    before = {p: p.read_bytes() for p in design}

    run = run_validate(project, gate="full", out=project.parent.parent / "out")

    st = {r.id: r.status for r in run.results}
    assert st["intake.tools"] == st["intake.files"] == "PASS"
    assert st["sch.erc"] != "ERROR" and st["pcb.drc"] != "ERROR" and st["fab.rules"] != "ERROR"
    assert run.verdict in ("PASS", "FAIL") and run.exit_code in (0, 1)
    assert (run.run_dir / "report.json").is_file() and (run.run_dir / "REPORT.md").is_file()
    assert (run.run_dir / "checks" / "pcb.drc" / "drc.json").is_file()
    # somente leitura: o projeto original não mudou
    assert {p: p.read_bytes() for p in before} == before
    assert not project.with_suffix(".kicad_dru").exists()


def test_validate_gerbers_stage_on_kicad_demo(demo_project, user_config) -> None:
    from mcp_kicad.validate import run_validate

    # O demo reprova DRC/regras JLCPCB; o usuário rebaixa os pré-requisitos para chegar ao Gerber.
    prereqs = ("sch.erc", "pcb.drc", "fab.rules")
    user_config({"checks": [{"id": c, "gate": "warn"} for c in prereqs]})
    project = demo_project("interf_u") / "interf_u.kicad_pro"
    run = run_validate(project, only=["fab.gerbers"], out=project.parent.parent / "out")
    st = {r.id: r.status for r in run.results}
    # --only inclui os pré-requisitos duros, mas não o irmão pcb.parity
    assert st["pcb.drc"] != "SKIPPED" and st["fab.rules"] != "SKIPPED"
    assert st["pcb.parity"] == "SKIPPED"
    result = next(r for r in run.results if r.id == "fab.gerbers")
    assert result.status == "PASS", result.error or [f.title for f in result.findings]
    assert result.data["files"]
