"""Testes de apply_rules (.bak) e export_fab_package (re-execução) em fab/jlcpcb.py.

Os exports do kicad-cli são substituídos por geradores falsos; a execução
contra o kicad-cli real está em test_integration_kicad.py.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from mcp_kicad.errors import KicadCliFailed
from mcp_kicad.fab import jlcpcb

# --- apply_rules: o .bak nunca sobrescreve as regras originais ---------------


def _board(tmp_path: Path) -> Path:
    board = tmp_path / "board.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    return board


def test_apply_rules_keeps_the_original_backup_on_second_call(tmp_path) -> None:
    board = _board(tmp_path)
    rules = tmp_path / "board.kicad_dru"
    rules.write_text("ORIGINAL_USER_RULES", encoding="utf-8")
    backup = tmp_path / "board.kicad_dru.bak"

    jlcpcb.apply_rules(board, "2layer")
    result = jlcpcb.apply_rules(board, "4layer")

    assert backup.read_text(encoding="utf-8") == "ORIGINAL_USER_RULES"
    assert rules.read_text(encoding="utf-8") == jlcpcb.rules_text("4layer")
    assert result["rules_file"] == str(rules)
    assert result["backup"] == str(backup)
    assert result["backup_preexistente"] is True


def test_apply_rules_first_call_creates_the_backup(tmp_path) -> None:
    board = _board(tmp_path)
    (tmp_path / "board.kicad_dru").write_text("ORIGINAL_USER_RULES", encoding="utf-8")

    result = jlcpcb.apply_rules(board, "2layer")

    assert (tmp_path / "board.kicad_dru.bak").read_text(encoding="utf-8") == "ORIGINAL_USER_RULES"
    assert result["backup"] == str(tmp_path / "board.kicad_dru.bak")
    assert result["backup_preexistente"] is False


def test_apply_rules_without_previous_file_makes_no_backup(tmp_path) -> None:
    result = jlcpcb.apply_rules(_board(tmp_path), "2layer")

    assert result == {"rules_file": str(tmp_path / "board.kicad_dru")}
    assert not (tmp_path / "board.kicad_dru.bak").exists()


# --- export_fab_package: re-executável e atômico -----------------------------

_LAYER_FILES = [
    "board-F_Cu.gbr",
    "board-B_Cu.gbr",
    "board-F_Mask.gbr",
    "board-B_Mask.gbr",
    "board-F_Silkscreen.gbr",
    "board-B_Silkscreen.gbr",
    "board-Edge_Cuts.gbr",
]


def _two_layer_board(tmp_path: Path) -> Path:
    board = tmp_path / "board.kicad_pcb"
    board.write_text(
        '(kicad_pcb (layers (0 "F.Cu" signal) (31 "B.Cu" signal)))', encoding="utf-8"
    )
    return board


def _fake_exporters(monkeypatch) -> None:
    def fake_gerbers(pcb, outdir, *, layers=None, protel_ext=True):
        for name in _LAYER_FILES:
            (Path(outdir) / name).write_text("G04*", encoding="utf-8")
        return Path(outdir)

    def fake_drill(pcb, outdir):
        (Path(outdir) / "board.drl").write_text("M48", encoding="utf-8")
        return Path(outdir)

    monkeypatch.setattr(jlcpcb.kicad_cli, "pcb_export_gerbers", fake_gerbers)
    monkeypatch.setattr(jlcpcb.kicad_cli, "pcb_export_drill", fake_drill)


def _zip_names(path: str) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return sorted(zf.namelist())


def test_export_fab_package_can_run_twice_in_the_same_directory(tmp_path, monkeypatch) -> None:
    _fake_exporters(monkeypatch)
    board = _two_layer_board(tmp_path)

    first = jlcpcb.export_fab_package(board, tmp_path / "production")
    second = jlcpcb.export_fab_package(board, tmp_path / "production")

    expected = sorted([*_LAYER_FILES, "board.drl"])
    assert _zip_names(first["zip"]) == expected
    assert _zip_names(second["zip"]) == expected
    assert "warning" not in second


def test_export_fab_package_leaves_no_temporary_leftovers(tmp_path, monkeypatch) -> None:
    _fake_exporters(monkeypatch)
    dest = tmp_path / "production"

    result = jlcpcb.export_fab_package(_two_layer_board(tmp_path), dest)

    assert _leftovers(dest) == []
    # os arquivos soltos continuam em `directory`, como antes
    assert (Path(result["directory"]) / "board-F_Cu.gbr").is_file()
    assert (Path(result["directory"]) / "board.drl").is_file()


def _leftovers(directory: Path) -> list[str]:
    """Temporários que a exportação deixou para trás: o `.fab-*` oculto e o zip parcial."""
    return sorted(
        p.name for p in directory.iterdir() if p.name.startswith(".") or p.suffix == ".tmp"
    )


def test_export_fab_package_exporter_failure_leaves_no_temporary_dir(tmp_path, monkeypatch) -> None:
    _fake_exporters(monkeypatch)
    board = _two_layer_board(tmp_path)
    dest = tmp_path / "production"
    good = Path(jlcpcb.export_fab_package(board, dest)["zip"])
    good_bytes = good.read_bytes()

    def failing_gerbers(pcb, outdir, *, layers=None, protel_ext=True):
        raise KicadCliFailed("kicad-cli falhou (3)", returncode=3)

    monkeypatch.setattr(jlcpcb.kicad_cli, "pcb_export_gerbers", failing_gerbers)

    with pytest.raises(KicadCliFailed):
        jlcpcb.export_fab_package(board, dest)

    assert good.read_bytes() == good_bytes
    assert _leftovers(dest) == []


def test_export_fab_package_failure_mid_zip_keeps_the_previous_good_zip(
    tmp_path, monkeypatch
) -> None:
    """A escrita do zip falha depois de gravar parte dos membros: o zip bom não pode ser tocado."""
    _fake_exporters(monkeypatch)
    board = _two_layer_board(tmp_path)
    dest = tmp_path / "production"
    good = Path(jlcpcb.export_fab_package(board, dest)["zip"])
    good_bytes = good.read_bytes()

    original_write = zipfile.ZipFile.write
    written: list[str] = []

    def flaky_write(self, filename, arcname=None, *args, **kwargs):
        if len(written) == 2:
            raise OSError("disco cheio")
        written.append(str(arcname))
        return original_write(self, filename, arcname, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", flaky_write)

    with pytest.raises(OSError, match="disco cheio"):
        jlcpcb.export_fab_package(board, dest)

    assert len(written) == 2  # falhou no meio, com membros já gravados
    assert good.read_bytes() == good_bytes
    assert _leftovers(dest) == []
