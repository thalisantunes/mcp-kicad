"""Plugins de revisão (S5): opcionais, locais, nenhum ativo por padrão (ADR 0002, item 8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..finding import Finding


class Reviewer(Protocol):
    """Lê o snapshot (somente leitura) e os relatórios dos builtins em `context`.

    `context` traz `check_id`, `profile` e `reports` (id do check → achados em dict).
    Devolve achados no schema comum, com `source="agent"`.
    """

    def run(self, snapshot: Path, context: dict[str, Any]) -> list[Finding]: ...
