"""Testes da consolidação de código LCSC e validação de saída em
src/mcp_kicad/fab/jlcpcb.py.
"""

from __future__ import annotations

import csv
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


def _fake_kicad_grouping(components: list[dict[str, str]]):
    """`sch_export_bom_fields` falso que agrupa como o kicad-cli.

    Componentes com a mesma chave de `group_by` viram uma linha. `Reference` e os
    demais campos juntam os valores distintos e não vazios com vírgula, que é o
    que o kicad-cli faz (ex.: LCSC "C15850,C99999").
    """

    def fake(sch, out_csv, *, fields, group_by=None, **kwargs):
        columns = fields.split(",")
        keys = group_by.split(",") if group_by else []
        groups: dict[tuple, list[dict[str, str]]] = {}
        for index, comp in enumerate(components):
            key = tuple(comp.get(k, "") for k in keys) if keys else (index,)
            groups.setdefault(key, []).append(comp)

        with Path(out_csv).open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            for members in groups.values():
                row = []
                for column in columns:
                    if column == "QUANTITY":
                        row.append(str(len(members)))
                        continue
                    values = [m.get(column, "") for m in members]
                    row.append(",".join(dict.fromkeys(v for v in values if v)))
                writer.writerow(row)
        return Path(out_csv)

    return fake


def test_export_bom_never_merges_different_lcsc_codes(tmp_path, monkeypatch) -> None:
    """Dois códigos LCSC no mesmo Value+Footprint não podem virar "C15850,C99999"."""
    schematic = tmp_path / "board.kicad_sch"
    schematic.write_text("(kicad_sch)", encoding="utf-8")
    base = {"Footprint": "C_0805", "Value": "10u"}
    components = [
        {**base, "Reference": "C1", "LCSC": "C15850"},
        {**base, "Reference": "C2", "LCSC": "C15850"},
        {**base, "Reference": "C3", "LCSC": "C99999"},
        {**base, "Reference": "C4", "LCSC": ""},
        {**base, "Reference": "C5", "LCSC": ""},
    ]
    monkeypatch.setattr(jlcpcb.kicad_cli, "sch_export_bom_fields", _fake_kicad_grouping(components))

    result = jlcpcb.export_bom(schematic, tmp_path / "bom.csv")

    with (tmp_path / "bom.csv").open(newline="", encoding="utf-8") as f:
        by_lcsc = {row["LCSC Part #"]: row for row in csv.DictReader(f)}
    assert set(by_lcsc) == {"C15850", "C99999", ""}
    assert by_lcsc["C15850"]["Designator"] == "C1,C2"
    assert by_lcsc["C15850"]["Quantity"] == "2"
    assert by_lcsc["C99999"]["Designator"] == "C3"
    # os itens sem LCSC dividem uma linha própria, em vez de herdar o código dos vizinhos
    assert by_lcsc[""]["Designator"] == "C4,C5"
    assert by_lcsc[""]["Quantity"] == "2"
    assert result["rows"] == 3
    # missing_lcsc conta linhas, não componentes: C4 e C5 são 1 linha
    assert result["missing_lcsc"] == 1
