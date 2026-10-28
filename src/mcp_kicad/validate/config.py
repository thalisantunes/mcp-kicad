"""Configuração do `validate`: duas fontes com confiança diferente (ADR 0002).

* **Usuário** (confiável): `$MCP_KICAD_REVIEWERS` (caminho de um JSON) ou
  `~/.config/mcp-kicad/reviewers.json`. Só aqui entram plugins (`kind: agent`, `command`) e
  só aqui um check builtin pode ser desabilitado ou rebaixado.

      {"checks": [
        {"id": "review.power", "stage": "S5", "kind": "agent", "requires": ["pcb.drc"],
         "gate": "warn", "command": ["claude", "-p"], "env_passthrough": ["ANTHROPIC_API_KEY"],
         "prompt": "Revise a alimentação.", "timeout_s": 600}]}

* **Projeto** (NÃO confiável: é o que está sendo validado): `<projeto>/.mcp-kicad/validate.json`.
  Só `fab_profile`, `skip`/`until` padrão e `{"id", "gate", "enabled"}` de checks não-builtin
  já definidos pela config do usuário. Qualquer outra coisa é `MK-VAL-001`.

JSON, não YAML: o PyYAML não é dependência do projeto.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..errors import ValidateGraphInvalid
from ..fab import jlcpcb
from .graph import DEFAULT_CHECKS, CheckSpec, Graph

CONFIG_RELPATH = Path(".mcp-kicad") / "validate.json"
USER_CONFIG_ENV = "MCP_KICAD_REVIEWERS"
USER_CONFIG_DEFAULT = Path("~/.config/mcp-kicad/reviewers.json")

BUILTIN_IDS = frozenset(c.id for c in DEFAULT_CHECKS)
_PROJECT_KEYS = frozenset({"fab_profile", "skip", "until", "checks"})
_PROJECT_CHECK_KEYS = frozenset({"id", "gate", "enabled"})
_WHERE_PLUGINS = f"{USER_CONFIG_ENV} ou {USER_CONFIG_DEFAULT}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidateGraphInvalid(f"{path}: JSON ilegível ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("checks", []), list):
        raise ValidateGraphInvalid(f"{path}: esperado objeto com 'checks' lista")
    return data


def load_user_config() -> dict[str, Any]:
    """Config do usuário (plugins e overrides livres); `{}` se não existir."""
    env = os.environ.get(USER_CONFIG_ENV)
    path = Path(env or USER_CONFIG_DEFAULT).expanduser()
    if not path.is_file():
        if env:
            raise ValidateGraphInvalid(f"{USER_CONFIG_ENV} aponta para arquivo inexistente: {path}")
        return {}
    data = _read_json(path)
    unknown = set(data) - {"checks"}
    if unknown:
        raise ValidateGraphInvalid(f"{path}: chaves desconhecidas {sorted(unknown)} (use 'checks')")
    return data


def load_project_config(project_dir: Path) -> dict[str, Any]:
    """Config do projeto, já restrita ao que um projeto não confiável pode pedir."""
    path = project_dir / CONFIG_RELPATH
    if not path.is_file():
        if path.with_suffix(".yaml").is_file():
            raise ValidateGraphInvalid(
                f"{path.with_suffix('.yaml')}: YAML não é suportado (PyYAML não é dependência); "
                f"converta para {path.name}"
            )
        return {}
    data = _read_json(path)
    unknown = set(data) - _PROJECT_KEYS
    if unknown:
        raise ValidateGraphInvalid(
            f"{path}: chaves não permitidas na config do projeto: {sorted(unknown)}. "
            f"Permitido: {sorted(_PROJECT_KEYS)}"
        )
    profile = data.get("fab_profile")
    if profile is not None and not (isinstance(profile, str) and profile in jlcpcb.PROFILES):
        raise ValidateGraphInvalid(
            f"{path}: fab_profile {profile!r} desconhecido (use {', '.join(jlcpcb.PROFILES)})"
        )
    until, skip = data.get("until"), data.get("skip")
    if until is not None and not isinstance(until, str):
        raise ValidateGraphInvalid(f"{path}: 'until' deve ser texto")
    if skip is not None and not (isinstance(skip, list) and all(isinstance(x, str) for x in skip)):
        raise ValidateGraphInvalid(f"{path}: 'skip' deve ser lista de textos")
    return data


def _apply(
    specs: dict[str, CheckSpec], entry: Any, source: str, overrides: list[dict[str, Any]]
) -> dict[str, CheckSpec]:
    if not isinstance(entry, dict) or "id" not in entry:
        raise ValidateGraphInvalid(f"entrada de checks inválida: {entry!r}")
    cid = entry["id"]
    if not isinstance(cid, str):
        raise ValidateGraphInvalid(f"id de check deve ser texto: {cid!r}")
    overrides.append({"source": source, **entry})
    if entry.get("enabled") is False:
        if cid not in specs:
            raise ValidateGraphInvalid(f"enabled=false em check desconhecido: '{cid}'")
        rest = {k: s for k, s in specs.items() if k != cid}
        return {
            k: replace(s, requires=tuple(r for r in s.requires if r != cid))
            for k, s in rest.items()
        }
    merged = specs[cid].to_dict() if cid in specs else {}
    merged.update({k: v for k, v in entry.items() if k != "enabled"})
    try:
        return {**specs, cid: CheckSpec.from_dict(merged)}
    except (TypeError, ValueError) as exc:  # ex.: requires: 5, gate: [] vindos do JSON
        raise ValidateGraphInvalid(f"check '{cid}': entrada inválida ({exc})") from exc


def _guard_project_entry(entry: Any, specs: dict[str, CheckSpec]) -> None:
    if not isinstance(entry, dict) or "id" not in entry:
        raise ValidateGraphInvalid(f"entrada de checks inválida: {entry!r}")
    cid = entry["id"]
    if not isinstance(cid, str):
        raise ValidateGraphInvalid(f"id de check deve ser texto: {cid!r}")
    if "command" in entry or entry.get("kind") == "agent":
        raise ValidateGraphInvalid(
            f"check '{cid}': a config do projeto não pode declarar 'command' nem 'kind: agent'. "
            "O projeto validado não é confiável e isso executaria um programa na sua máquina; "
            f"plugins só vêm da config do usuário ({_WHERE_PLUGINS})."
        )
    if cid in BUILTIN_IDS:
        raise ValidateGraphInvalid(
            f"check '{cid}' é builtin: a config do projeto não pode desabilitá-lo, rebaixá-lo "
            f"nem alterá-lo. Só a config do usuário ({_WHERE_PLUGINS}) pode."
        )
    if cid not in specs:
        raise ValidateGraphInvalid(
            f"check '{cid}': a config do projeto não pode criar checks, só ajustar os já "
            f"definidos na config do usuário ({_WHERE_PLUGINS})."
        )
    extra = sorted(set(entry) - _PROJECT_CHECK_KEYS)
    if extra:
        raise ValidateGraphInvalid(
            f"check '{cid}': chaves não permitidas na config do projeto: {extra} "
            f"(permitido: {sorted(_PROJECT_CHECK_KEYS)})"
        )


def build_graph(user: dict[str, Any] | None = None, project: dict[str, Any] | None = None) -> Graph:
    """Grafo padrão + config do usuário (livre) + config do projeto (restrita).

    `graph.overrides` lista cada entrada aceita, com a origem, para o relatório.
    """
    specs = {c.id: c for c in DEFAULT_CHECKS}
    overrides: list[dict[str, Any]] = []
    for entry in (user or {}).get("checks", []):
        specs = _apply(specs, entry, "user", overrides)
    for entry in (project or {}).get("checks", []):
        _guard_project_entry(entry, specs)
        specs = _apply(specs, entry, "project", overrides)
    graph = Graph(list(specs.values()))
    graph.overrides = overrides
    return graph
