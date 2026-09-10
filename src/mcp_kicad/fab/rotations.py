"""Correção de rotação de footprint para o CPL da JLCPCB.

O KiCad grava no CPL o ângulo do footprint como desenhado no board. A
montadora espera outra convenção para vários encapsulamentos (SOT-23, DFN,
QFN...) — sem a correção, a máquina de pick-and-place gira o componente
errado e a solda fica invertida.

A tabela de correção usada aqui é uma **porta de ideia** do
[`matthewlai/JLCKicadTools`](https://github.com/matthewlai/JLCKicadTools)
(`jlc_kicad_tools/cpl_rotations_db.csv`, commit `500a9ff`), que é GPL-3.0.
Este projeto é MIT, então o CSV **não é embarcado no repo**: é baixado sob
demanda com `fetch_table()` e cacheado em `$XDG_CACHE_HOME/mcp-kicad/`
(fallback `~/.cache/mcp-kicad/`). A lógica de matching e de correção abaixo
foi escrita do zero — só a ideia vem do projeto de origem, não a expressão.

Ver docs/rotacoes-cpl.md para a matemática completa e as armadilhas
verificadas contra o KiCad 10.0.5.
"""

from __future__ import annotations

import csv
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..errors import RotationTableInvalid, RotationTableUnavailable

TABLE_URL = (
    "https://raw.githubusercontent.com/matthewlai/JLCKicadTools/"
    "master/jlc_kicad_tools/cpl_rotations_db.csv"
)
_FETCH_TIMEOUT_S = 10
_CACHE_FILENAME = "cpl_rotations_db.csv"


@dataclass(frozen=True, slots=True)
class Correction:
    pattern: str
    rotation: float
    offset_x: float = 0.0
    offset_y: float = 0.0


def _cache_path() -> Path:
    """Caminho do CSV cacheado: $XDG_CACHE_HOME/mcp-kicad/, senão ~/.cache/mcp-kicad/."""
    base = os.environ.get("XDG_CACHE_HOME")
    cache_dir = (
        Path(base).expanduser() / "mcp-kicad" if base else Path.home() / ".cache" / "mcp-kicad"
    )
    return cache_dir / _CACHE_FILENAME


def fetch_table(force: bool = False) -> Path:
    """Baixa a tabela de correção para o cache local.

    Não rebaixa se já existe, a menos que `force=True`. Timeout de 10s — falha
    de rede vira `RotationTableUnavailable`, nunca stack trace cru.
    """
    path = _cache_path()
    if path.is_file() and not force:
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(TABLE_URL, timeout=_FETCH_TIMEOUT_S) as resp:  # noqa: S310
            data = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise RotationTableUnavailable(
            f"falha ao baixar tabela de rotação de {TABLE_URL}: {exc}"
        ) from exc

    # Escreve em tmp + replace: um download interrompido não deixa cache
    # parcial no lugar do arquivo bom que já existia.
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def load_table() -> list[Correction]:
    """Lê o CSV cacheado. Levanta erro pedindo `fetch_table()` se não existe.

    Linhas vazias são ignoradas. As colunas de offset são opcionais por linha
    (a maioria do CSV só tem padrão + rotação) — default 0.0. Uma linha com
    campo numérico ilegível é pulada, não derruba a tabela inteira. Se
    NENHUMA linha sobrar depois do parsing (cache truncado, só header, ou
    tudo malformado), levanta `RotationTableInvalid` — devolver `[]` aqui
    seria indistinguível de "tabela ok, nenhum footprint precisa de
    correção".
    """
    path = _cache_path()
    if not path.is_file():
        raise RotationTableUnavailable(
            f"tabela de rotação não está em cache ({path}). Rode "
            "jlcpcb_fetch_rotations primeiro."
        )

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RotationTableInvalid(f"não foi possível ler {path}: {exc}") from exc

    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise RotationTableInvalid(f"CSV vazio: {path}")

    corrections: list[Correction] = []
    for row in rows[1:]:  # rows[0] é o header
        if not row or not any(cell.strip() for cell in row):
            continue
        pattern = row[0].strip()
        if not pattern:
            continue
        try:
            rotation = float(row[1].strip())
            offset_x = float(row[2].strip()) if len(row) > 2 and row[2].strip() else 0.0
            offset_y = float(row[3].strip()) if len(row) > 3 and row[3].strip() else 0.0
        except (IndexError, ValueError):
            continue
        corrections.append(Correction(pattern, rotation, offset_x, offset_y))

    if not corrections:
        # Cache existe e tem header, mas nenhuma linha de dado sobreviveu ao
        # parsing (só header, ou tudo malformado). Sem isso, export_cpl não
        # tem como distinguir "tabela inutilizável" de "nenhum footprint
        # precisava de correção" — os dois dão corrections_applied: [].
        raise RotationTableInvalid(
            f"cache em {path} não tem nenhuma linha de correção utilizável "
            "(só header, ou todas as linhas malformadas). Rode "
            "jlcpcb_fetch_rotations(force=True) para rebaixar."
        )

    return corrections


def find_correction(footprint: str, table: list[Correction]) -> Correction | None:
    """Casa `footprint` contra a tabela, em dois passes nesta ordem exata.

    1. Cada padrão testado como `(?:padrão)$` — o footprint precisa terminar
       exatamente ali. Isso naturalmente favorece a entrada mais específica:
       um padrão genérico como `^SOT-23` só passa aqui se o footprint for
       literalmente "SOT-23", enquanto um padrão específico como
       `^SOT-23-5$` passa para "SOT-23-5".
    2. Se nada casou no passe 1, testa o padrão cru (`re.search`) — fallback
       por prefixo/substring (ex: `^SOT-23` casa com "SOT-23-5").

    A primeira entrada que casar em cada passe vence — não acumula correções.
    Regex inválido numa linha é ignorado, não derruba o matching inteiro.
    """
    for correction in table:
        try:
            if re.search(f"(?:{correction.pattern})$", footprint):
                return correction
        except re.error:
            continue

    for correction in table:
        try:
            if re.search(correction.pattern, footprint):
                return correction
        except re.error:
            continue

    return None


def corrected_rotation(rot: float, side: str, correction: Correction | None) -> float:
    """Rotação final para o CPL, sempre em [0, 360).

    Ordem das operações (importa — trocar a ordem dá resultado diferente):

    1. Se `side == "bottom"`: espelha o ângulo com `(180 - rot) % 360`. O
       kicad-cli NÃO faz isso sozinho — ele exporta o ângulo cru do
       footprint mesmo em B.Cu.
    2. Se há correção na tabela, soma `correction.rotation`.
    3. Normaliza para [0, 360), com ou sem correção.
    """
    if side == "bottom":
        rot = (180 - rot) % 360
    if correction is not None:
        rot = (rot + correction.rotation) % 360
    return rot % 360
