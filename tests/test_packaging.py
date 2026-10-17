"""Metadados de empacotamento: mesma versão nos quatro lugares e os dois nomes de comando.

O `uvx mcp-kicad10` do server.json só funciona se existir um executável com o
nome da distribuição; `mcp-kicad` continua valendo para o `uv run`.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import mcp_kicad

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_is_the_same_in_pyproject_init_and_server_json() -> None:
    server_json = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))

    versions = {
        "pyproject": _pyproject()["project"]["version"],
        "__init__": mcp_kicad.__version__,
        "server.json": server_json["version"],
        "server.json packages[0]": server_json["packages"][0]["version"],
    }

    assert len(set(versions.values())) == 1, versions


def test_both_command_names_point_to_the_same_entry_point() -> None:
    project = _pyproject()["project"]

    assert project["name"] == "mcp-kicad10"
    assert project["scripts"]["mcp-kicad"] == "mcp_kicad.__main__:main"
    assert project["scripts"]["mcp-kicad10"] == "mcp_kicad.__main__:main"


def test_dependencies_have_an_upper_bound() -> None:
    dependencies = _pyproject()["project"]["dependencies"]

    assert "mcp>=2.0.0,<3" in dependencies
    assert "kicad-python>=0.7.1,<1" in dependencies
