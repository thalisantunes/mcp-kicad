"""Testes da consolidação de código LCSC e validação de saída em
src/mcp_kicad/fab/jlcpcb.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_kicad.errors import UnexpectedCliOutput
from mcp_kicad.fab import jlcpcb
from mcp_kicad.fab.jlcpcb import LCSC_FIELDS, _consolidate_lcsc


def test_consolidate_lcsc_picks_first_non_empty_in_field_order() -> None:
    row = {
        "LCSC": "",
        "LCSC Part #": "C123456",
        "LCSC#": "C999999",
        "JLCPCB Part #": "C888888",
    }

    assert _consolidate_lcsc(row) == "C123456"


def test_consolidate_lcsc_uses_first_field_when_present() -> None:
    row = {"LCSC": "C1", "LCSC Part #": "C2", "LCSC#": "C3", "JLCPCB Part #": "C4"}

    assert _consolidate_lcsc(row) == "C1"


def test_consolidate_lcsc_falls_back_to_last_field() -> None:
    row = {"LCSC": "", "LCSC Part #": "", "LCSC#": "", "JLCPCB Part #": "C4"}

    assert _consolidate_lcsc(row) == "C4"


def test_consolidate_lcsc_empty_when_no_field_set() -> None:
    row = {field: "" for field in LCSC_FIELDS}

    assert _consolidate_lcsc(row) == ""


def test_consolidate_lcsc_missing_fields_treated_as_empty() -> None:
    """Linha sem alguma das colunas (dict.get) não derruba a consolidação."""
    row = {"Value": "100n"}

    assert _consolidate_lcsc(row) == ""


def test_export_bom_missing_column_raises_unexpected_cli_output(tmp_path, monkeypatch) -> None:
    """kicad-cli hipotético que renomeou/removeu QUANTITY vira MK-FAB-004, não KeyError cru."""
    schematic = tmp_path / "board.kicad_sch"
    schematic.write_text("(kicad_sch)", encoding="utf-8")

    # Sem a coluna QUANTITY.
    raw_csv = 'Reference,Footprint,Value,LCSC,"LCSC Part #",LCSC#,"JLCPCB Part #"\n'
    raw_csv += '"R1","R_0402",100,"","","",""\n'

    def _fake_sch_export_bom_fields(sch, out_csv, **kwargs):
        Path(out_csv).write_text(raw_csv, encoding="utf-8")
        return Path(out_csv)

    monkeypatch.setattr(jlcpcb.kicad_cli, "sch_export_bom_fields", _fake_sch_export_bom_fields)

    with pytest.raises(UnexpectedCliOutput) as exc_info:
        jlcpcb.export_bom(schematic, tmp_path / "bom.csv")

    assert "QUANTITY" in str(exc_info.value)
