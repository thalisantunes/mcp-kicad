"""Testes de src/mcp_kicad/fab/jlcpcb.py — export_cpl.

`kicad_cli.pcb_export_pos` e `rotations.load_table` são monkeypatched: os
testes não chamam o kicad-cli real nem tocam rede, só exercitam o
pós-processamento (matching de correção, offset, validação de coluna).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_kicad.errors import RotationTableUnavailable, UnexpectedCliOutput
from mcp_kicad.fab import jlcpcb
from mcp_kicad.fab.rotations import Correction


def _make_board(tmp_path: Path) -> Path:
    board = tmp_path / "board.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    return board


def _fake_pcb_export_pos(csv_text: str):
    def _fn(pcb, out_csv, *, use_drill_file_origin=True, exclude_dnp=True):
        Path(out_csv).write_text(csv_text, encoding="utf-8")
        return Path(out_csv)

    return _fn


def test_export_cpl_applies_rotation_without_offset(tmp_path, monkeypatch) -> None:
    board = _make_board(tmp_path)
    raw_csv = 'Ref,Val,Package,PosX,PosY,Rot,Side\n"U1","x","SOT-23-5",1.0,2.0,180.000000,top\n'
    monkeypatch.setattr(jlcpcb.kicad_cli, "pcb_export_pos", _fake_pcb_export_pos(raw_csv))
    monkeypatch.setattr(
        jlcpcb.rotations, "load_table", lambda: [Correction(pattern="^SOT-23", rotation=100)]
    )

    result = jlcpcb.export_cpl(board, tmp_path / "cpl.csv")

    assert result["rows"] == 1
    assert "warning" not in result
    [entry] = result["corrections_applied"]
    assert entry["designator"] == "U1"
    assert entry["rotation_original"] == 180.0
    assert entry["rotation_corrigida"] == 280.0  # 180 + 100, top (sem flip)
    assert "offset_pendente" not in entry

    out_text = (tmp_path / "cpl.csv").read_text(encoding="utf-8")
    assert "U1" in out_text
    assert "280.00" in out_text
    # o CSV cru intermediário não deve sobrar
    assert not (tmp_path / "cpl.raw.csv").exists()


def test_export_cpl_offset_correction_marked_pending_and_warns(tmp_path, monkeypatch) -> None:
    board = _make_board(tmp_path)
    raw_csv = (
        'Ref,Val,Package,PosX,PosY,Rot,Side\n'
        '"J1","x","TESTOFFSET_2x2mm",1.0,2.0,0.000000,top\n'
    )
    monkeypatch.setattr(jlcpcb.kicad_cli, "pcb_export_pos", _fake_pcb_export_pos(raw_csv))
    monkeypatch.setattr(
        jlcpcb.rotations,
        "load_table",
        lambda: [Correction(pattern="^TESTOFFSET", rotation=50, offset_x=1.44, offset_y=0.0)],
    )

    result = jlcpcb.export_cpl(board, tmp_path / "cpl.csv")

    [entry] = result["corrections_applied"]
    assert entry["designator"] == "J1"
    assert entry["rotation_corrigida"] == 50.0
    assert entry["offset_pendente"] == {"offset_x": 1.44, "offset_y": 0.0}

    assert "warning" in result
    assert "J1" in result["warning"]
    assert "offset" in result["warning"].lower()

    # posição gravada NÃO leva o offset — só PosX/PosY crus do kicad-cli
    out_text = (tmp_path / "cpl.csv").read_text(encoding="utf-8")
    assert "1.0000,2.0000" in out_text


def test_export_cpl_table_unavailable_still_generates_with_warning(tmp_path, monkeypatch) -> None:
    board = _make_board(tmp_path)
    raw_csv = 'Ref,Val,Package,PosX,PosY,Rot,Side\n"U1","x","SOT-23-5",1.0,2.0,180.000000,top\n'
    monkeypatch.setattr(jlcpcb.kicad_cli, "pcb_export_pos", _fake_pcb_export_pos(raw_csv))

    def _raise():
        raise RotationTableUnavailable("cache ausente")

    monkeypatch.setattr(jlcpcb.rotations, "load_table", lambda: _raise())

    result = jlcpcb.export_cpl(board, tmp_path / "cpl.csv")

    assert result["rows"] == 1
    assert result["corrections_applied"] == []
    assert "warning" in result
    assert "tabela indisponível" in result["warning"]


def test_export_cpl_missing_column_raises_unexpected_cli_output(tmp_path, monkeypatch) -> None:
    board = _make_board(tmp_path)
    # kicad-cli hipotético que removeu a coluna Rot
    raw_csv = 'Ref,Val,Package,PosX,PosY,Side\n"U1","x","SOT-23-5",1.0,2.0,top\n'
    monkeypatch.setattr(jlcpcb.kicad_cli, "pcb_export_pos", _fake_pcb_export_pos(raw_csv))
    monkeypatch.setattr(jlcpcb.rotations, "load_table", lambda: [])

    with pytest.raises(UnexpectedCliOutput) as exc_info:
        jlcpcb.export_cpl(board, tmp_path / "cpl.csv")

    assert "Rot" in str(exc_info.value)


def test_export_cpl_non_numeric_rot_raises_unexpected_cli_output(tmp_path, monkeypatch) -> None:
    board = _make_board(tmp_path)
    raw_csv = 'Ref,Val,Package,PosX,PosY,Rot,Side\n"U1","x","SOT-23-5",1.0,2.0,not-a-number,top\n'
    monkeypatch.setattr(jlcpcb.kicad_cli, "pcb_export_pos", _fake_pcb_export_pos(raw_csv))
    monkeypatch.setattr(
        jlcpcb.rotations, "load_table", lambda: [Correction(pattern="^SOT-23", rotation=100)]
    )

    with pytest.raises(UnexpectedCliOutput) as exc_info:
        jlcpcb.export_cpl(board, tmp_path / "cpl.csv")

    assert "U1" in str(exc_info.value)
    assert "Rot" in str(exc_info.value)
