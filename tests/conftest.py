"""Fixtures compartilhadas e skip automático dos testes de integração."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from mcp.client.stdio import StdioServerParameters

from mcp_kicad import kicad_cli
from mcp_kicad.errors import KicadCliNotFound

# Onde os pacotes oficiais do KiCad instalam os projetos de demo.
_DEMO_DIRS = [
    Path("/usr/share/kicad/demos"),
    Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/demos"),  # DMG do macOS
    Path("/Library/Application Support/kicad/demos"),  # cask do Homebrew
]


def pytest_collection_modifyitems(config, items) -> None:
    """Pula os testes `integration` quando não há kicad-cli (CI sem KiCad)."""
    try:
        kicad_cli.resolve_cli()
    except KicadCliNotFound:
        skip = pytest.mark.skip(reason="kicad-cli não encontrado")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def demo_project(tmp_path: Path):
    """Devolve `copy(nome)`: cópia gravável de um projeto de demo do KiCad.

    Os demos instalados são somente leitura e várias tools gravam ao lado do
    projeto, então os testes sempre trabalham numa cópia em tmp_path.
    """

    def copy(name: str) -> Path:
        for base in _DEMO_DIRS:
            source = base / name
            if source.is_dir():
                target = tmp_path / name
                shutil.copytree(source, target)
                return target
        pytest.skip(f"demo '{name}' não encontrado")

    return copy


@pytest.fixture
def server_params() -> StdioServerParameters:
    """Servidor MCP como processo filho, para falar JSON-RPC por stdio."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_kicad"],
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
