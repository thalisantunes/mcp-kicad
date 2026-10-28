"""Grafo declarativo de checks (ADR 0002, item 2). Configuração e override: `config.py`."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..errors import ValidateGraphInvalid

STAGES = ("S0", "S1", "S2", "S3", "S4", "S5")
STAGE_NAMES = {
    "S0": "intake",
    "S1": "esquema",
    "S2": "placa",
    "S3": "fabricação",
    "S4": "saída",
    "S5": "revisão",
}
GATES = ("block", "warn")
KINDS = ("builtin", "agent")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")  # vira nome de diretório
_CORE = {"id", "stage", "requires", "gate", "kind", "title"}


@dataclass(frozen=True, slots=True)
class CheckSpec:
    id: str
    stage: str
    requires: tuple[str, ...] = ()
    gate: str = "block"
    kind: str = "builtin"
    title: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckSpec:
        cid = data.get("id")
        if not isinstance(cid, str) or not _ID_RE.match(cid):
            raise ValidateGraphInvalid(f"id de check inválido: {cid!r}")
        spec = cls(
            id=cid,
            stage=str(data.get("stage", "")),
            requires=tuple(data.get("requires", ())),
            gate=data.get("gate", "block"),
            kind=data.get("kind", "builtin"),
            title=data.get("title", ""),
            options={k: v for k, v in data.items() if k not in _CORE},
        )
        if spec.stage not in STAGES:
            raise ValidateGraphInvalid(
                f"check '{cid}': estágio {spec.stage!r} inválido (use {', '.join(STAGES)})"
            )
        if spec.gate not in GATES or spec.kind not in KINDS:
            raise ValidateGraphInvalid(f"check '{cid}': gate/kind inválido")
        return spec

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "stage": self.stage,
            "requires": list(self.requires),
            "gate": self.gate,
            "kind": self.kind,
            "title": self.title,
            **self.options,
        }


def _b(cid: str, stage: str, requires: tuple[str, ...], title: str, gate: str = "block"):
    return CheckSpec(cid, stage, requires, gate, "builtin", title)


_INTAKE = ("intake.tools", "intake.files")

DEFAULT_CHECKS: tuple[CheckSpec, ...] = (
    _b("intake.tools", "S0", (), "kicad-cli presente e versão >= 10"),
    _b("intake.files", "S0", (), "arquivos .kicad_pro/.kicad_sch/.kicad_pcb existem"),
    _b("sch.erc", "S1", _INTAKE, "ERC do esquemático"),
    _b("pcb.drc", "S2", (*_INTAKE, "sch.erc"), "DRC da placa (inclui .kicad_dru do projeto)"),
    _b("pcb.parity", "S2", (*_INTAKE, "sch.erc"), "paridade esquema/placa"),
    _b("fab.rules", "S3", ("pcb.drc",), "DRC com o perfil JLCPCB aplicado numa cópia"),
    _b("fab.gerbers", "S4", ("fab.rules",), "export de Gerber/drill e camadas presentes"),
)


class Graph:
    def __init__(self, checks: list[CheckSpec]) -> None:
        self.checks = list(checks)
        self.by_id = {c.id: c for c in self.checks}
        self.overrides: list[dict[str, Any]] = []  # preenchido por config.build_graph
        self._validate()
        self._order = self._toposort()

    def _validate(self) -> None:
        if len(self.by_id) != len(self.checks):
            raise ValidateGraphInvalid("ids de check duplicados no grafo")
        for c in self.checks:
            if c.stage not in STAGES:
                raise ValidateGraphInvalid(f"check '{c.id}': estágio {c.stage!r} inválido")
            for dep in c.requires:
                if dep not in self.by_id:
                    raise ValidateGraphInvalid(f"check '{c.id}' requer '{dep}', que não existe")
                if dep == c.id:
                    raise ValidateGraphInvalid(f"check '{c.id}' requer a si mesmo")
                if STAGES.index(self.by_id[dep].stage) > STAGES.index(c.stage):
                    raise ValidateGraphInvalid(
                        f"check '{c.id}' ({c.stage}) requer '{dep}' de estágio posterior "
                        f"({self.by_id[dep].stage}): dependência só pode apontar para o mesmo "
                        "estágio ou um anterior"
                    )

    def _toposort(self) -> list[CheckSpec]:
        """Kahn estável: entre os prontos, menor estágio primeiro, depois a ordem declarada."""
        pending = {c.id: set(c.requires) for c in self.checks}
        index = {c.id: i for i, c in enumerate(self.checks)}
        done: list[CheckSpec] = []
        while pending:
            ready = sorted(
                (cid for cid, deps in pending.items() if not deps),
                key=lambda cid: (STAGES.index(self.by_id[cid].stage), index[cid]),
            )
            if not ready:
                raise ValidateGraphInvalid(f"ciclo no grafo entre: {', '.join(sorted(pending))}")
            nxt = ready[0]
            done.append(self.by_id[nxt])
            del pending[nxt]
            for deps in pending.values():
                deps.discard(nxt)
        return done

    def order(self) -> list[CheckSpec]:
        return list(self._order)

    def resolve(self, names: list[str] | tuple[str, ...], *, flag: str) -> set[str]:
        """Expande ids de check e estágios (S2) para um conjunto de ids de check."""
        found: set[str] = set()
        for name in names:
            if name in self.by_id:
                found.add(name)
            elif name in STAGES:
                found |= {c.id for c in self.checks if c.stage == name}
            else:
                raise ValidateGraphInvalid(
                    f"{flag}: '{name}' não é check nem estágio. Checks: {', '.join(self.by_id)}"
                )
        return found

    def with_prerequisites(self, ids: set[str]) -> set[str]:
        """`ids` mais todos os pré-requisitos duros (transitivos)."""
        out: set[str] = set()
        stack = list(ids)
        while stack:
            cid = stack.pop()
            if cid not in out:
                out.add(cid)
                stack.extend(self.by_id[cid].requires)
        return out
