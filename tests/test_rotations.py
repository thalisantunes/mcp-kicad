"""Testes de src/mcp_kicad/fab/rotations.py.

Sem rede: tabelas fake em memória ou em tmp_path, nunca fetch_table() real.

Os padrões abaixo (`^SOT-23`, `^DFN-`) são iguais aos da tabela real
(matthewlai/JLCKicadTools, GPL-3.0) — necessários para testar o matching
contra strings reais. As ROTAÇÕES usadas são arbitrárias, não os valores
reais da tabela: reproduzir o par (padrão, rotação) literal da fonte GPL
num teste deste repo MIT seria reproduzir o dado, não só a ideia.
"""

from __future__ import annotations

import pytest

from mcp_kicad.errors import RotationTableInvalid
from mcp_kicad.fab.rotations import Correction, corrected_rotation, find_correction, load_table


def test_find_correction_fallback_substring() -> None:
    """`^SOT-23` sem correspondente específico casa com "SOT-23-5" no passe 2."""
    table = [Correction(pattern="^SOT-23", rotation=137)]

    result = find_correction("SOT-23-5", table)

    assert result is not None
    assert result.rotation == 137


def test_find_correction_specific_wins_over_generic() -> None:
    """Com ambas as entradas na tabela, a específica (passe 1, ancorada) vence."""
    table = [
        Correction(pattern="^SOT-23", rotation=137),
        Correction(pattern="^SOT-23-5$", rotation=42),
    ]

    result = find_correction("SOT-23-5", table)

    assert result is not None
    assert result.rotation == 42


def test_find_correction_dfn_hyphen_regression() -> None:
    """`^DFN-` não pode casar com "DFN2510A-10" — falta o hífen logo após DFN."""
    table = [Correction(pattern="^DFN-", rotation=123)]

    result = find_correction("DFN2510A-10_L2.5-W1.0-P0.50-BL", table)

    assert result is None


def test_find_correction_invalid_regex_is_skipped() -> None:
    """Uma linha com regex inválido não derruba o matching das demais."""
    table = [
        Correction(pattern="^SOT-23(", rotation=-1),  # regex inválido: parêntese aberto
        Correction(pattern="^SOT-23", rotation=137),
    ]

    result = find_correction("SOT-23-5", table)

    assert result is not None
    assert result.rotation == 137


def test_find_correction_no_match_returns_none() -> None:
    table = [Correction(pattern="^SOT-23", rotation=137)]

    assert find_correction("QFN-32", table) is None


def test_corrected_rotation_bottom_flip_before_correction() -> None:
    """Bottom espelha (180 - rot) % 360 e só depois soma a correção da tabela."""
    correction = Correction(pattern="^X", rotation=270)

    # rot=0, bottom -> flip para 180, depois +270 = 450 % 360 = 90
    assert corrected_rotation(0, "bottom", correction) == 90


def test_corrected_rotation_top_no_flip() -> None:
    correction = Correction(pattern="^X", rotation=-90)

    assert corrected_rotation(180, "top", correction) == 90


def test_corrected_rotation_without_correction_still_normalizes() -> None:
    assert corrected_rotation(0, "bottom", None) == 180


def test_corrected_rotation_always_in_range_negative_correction() -> None:
    correction = Correction(pattern="^X", rotation=-90)

    result = corrected_rotation(-90, "top", correction)

    assert 0 <= result < 360
    # -90 + (-90) = -180 -> normaliza para 180
    assert result == 180


def test_corrected_rotation_always_in_range_negative_rot_no_correction() -> None:
    result = corrected_rotation(-30, "top", None)

    assert 0 <= result < 360
    assert result == 330


def test_load_table_ignores_blank_lines_and_bad_rows(tmp_path, monkeypatch) -> None:
    csv_text = (
        '"Footprint pattern","Rotation","Offset X","Offset Y"\n'
        '"^SOT-23",137\n'
        "\n"
        '"^BROKEN_NUMBER",not-a-number\n'
        '"^WITH_OFFSET",55,1.25,0.4\n'
    )
    cache_dir = tmp_path / "mcp-kicad"
    cache_dir.mkdir()
    cache_file = cache_dir / "cpl_rotations_db.csv"
    cache_file.write_text(csv_text, encoding="utf-8")

    monkeypatch.setattr("mcp_kicad.fab.rotations._cache_path", lambda: cache_file)

    table = load_table()

    patterns = {c.pattern: c for c in table}
    assert "^SOT-23" in patterns
    assert patterns["^SOT-23"].rotation == 137
    assert "^BROKEN_NUMBER" not in patterns
    assert "^WITH_OFFSET" in patterns
    assert patterns["^WITH_OFFSET"].offset_x == 1.25
    assert patterns["^WITH_OFFSET"].offset_y == 0.4
    # linha sem offset -> default 0.0
    assert patterns["^SOT-23"].offset_x == 0.0
    assert patterns["^SOT-23"].offset_y == 0.0


def test_load_table_header_only_raises_invalid(tmp_path, monkeypatch) -> None:
    """Cache truncado (só header, sem nenhuma linha de dado) não pode virar []."""
    cache_dir = tmp_path / "mcp-kicad"
    cache_dir.mkdir()
    cache_file = cache_dir / "cpl_rotations_db.csv"
    cache_file.write_text(
        '"Footprint pattern","Rotation","Offset X","Offset Y"\n', encoding="utf-8"
    )

    monkeypatch.setattr("mcp_kicad.fab.rotations._cache_path", lambda: cache_file)

    with pytest.raises(RotationTableInvalid):
        load_table()


def test_load_table_all_rows_malformed_raises_invalid(tmp_path, monkeypatch) -> None:
    """Cache com header mas todas as linhas ilegíveis também não pode virar []."""
    csv_text = (
        '"Footprint pattern","Rotation","Offset X","Offset Y"\n'
        '"^A",not-a-number\n'
        '"^B",also-not-a-number\n'
    )
    cache_dir = tmp_path / "mcp-kicad"
    cache_dir.mkdir()
    cache_file = cache_dir / "cpl_rotations_db.csv"
    cache_file.write_text(csv_text, encoding="utf-8")

    monkeypatch.setattr("mcp_kicad.fab.rotations._cache_path", lambda: cache_file)

    with pytest.raises(RotationTableInvalid):
        load_table()
