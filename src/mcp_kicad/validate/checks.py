"""Checks builtin do grafo padrão. Roteamento (ADR 0002): arquivo → kicad_cli, no snapshot."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import kicad_cli
from ..errors import KicadCliNotFound
from ..fab import jlcpcb
from .finding import Finding, from_kicad
from .snapshot import Snapshot

MIN_KICAD_MAJOR = 10


@dataclass
class CheckOutput:
    findings: list[Finding] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Context:
    """O que um check enxerga: só caminhos DENTRO do snapshot e a pasta de saída do run."""

    snapshot: Snapshot
    project: Path  # .kicad_pro no snapshot
    run_dir: Path
    config: dict[str, Any] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)  # CheckResult já concluídos
    shared: dict[str, Any] = field(default_factory=dict)

    @property
    def sch(self) -> Path:
        return self.project.with_suffix(".kicad_sch")

    @property
    def pcb(self) -> Path:
        return self.project.with_suffix(".kicad_pcb")

    @property
    def fab_profile(self) -> str:
        return self.config.get("fab_profile", jlcpcb.DEFAULT_PROFILE)

    def check_dir(self, check_id: str) -> Path:
        d = self.run_dir / "checks" / check_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def drc_report(self) -> dict[str, Any]:
        """DRC completo (com paridade) do snapshot; roda uma vez e é compartilhado."""
        if "drc" not in self.shared:
            out = self.check_dir("pcb.drc") / "drc.json"
            self.shared["drc"] = kicad_cli.drc_report(self.pcb, out, parity=self.sch.is_file())
        return self.shared["drc"]


CheckFn = Callable[[Context], CheckOutput]


def _findings(
    check_id: str, violations: list[dict], *, warning_as: str, sheet=None
) -> list[Finding]:
    found = (
        from_kicad(check_id, i, v, warning_as=warning_as, sheet=sheet)
        for i, v in enumerate(violations, 1)
    )
    return [f for f in found if f is not None]


# --- S0 --------------------------------------------------------------------


def intake_tools(ctx: Context) -> CheckOutput:
    mk = lambda title, action: Finding(  # noqa: E731
        "intake.tools/001", "P0", title, rule="kicad-cli", suggested_action=action
    )
    try:
        version = kicad_cli.version()
    except KicadCliNotFound as exc:
        return CheckOutput([mk("kicad-cli não encontrado", str(exc))])
    ctx.shared["kicad_version"] = version
    match = re.match(r"(\d+)\.", version)
    if not match or int(match.group(1)) < MIN_KICAD_MAJOR:
        return CheckOutput(
            [
                mk(
                    f"KiCad {version!r} é anterior ao {MIN_KICAD_MAJOR}",
                    "Instalar KiCad 10 ou mais novo",
                )
            ],
            {"kicad_cli": version},
        )
    return CheckOutput(data={"kicad_cli": version})


def intake_files(ctx: Context) -> CheckOutput:
    missing = [p for p in (ctx.project, ctx.sch, ctx.pcb) if not p.is_file()]
    findings = [
        Finding(
            f"intake.files/{i:03d}",
            "P0",
            f"arquivo ausente: {p.name}",
            rule="project-files",
            suggested_action=f"Criar ou restaurar {p.name} ao lado do .kicad_pro",
        )
        for i, p in enumerate(missing, 1)
    ]
    return CheckOutput(findings)


# --- S1 / S2 ---------------------------------------------------------------


def sch_erc(ctx: Context) -> CheckOutput:
    out = ctx.check_dir("sch.erc") / "erc.json"
    report = kicad_cli.erc_report(ctx.sch, out)
    findings: list[Finding] = []
    for sheet in report.get("sheets", []):
        findings += _findings(
            "sch.erc", sheet.get("violations", []), warning_as="P2", sheet=sheet.get("path")
        )
    for i, f in enumerate(findings, 1):  # numeração contínua entre folhas
        f.id = f"sch.erc/{i:03d}"
    return CheckOutput(findings)


def pcb_drc(ctx: Context) -> CheckOutput:
    report = ctx.drc_report()
    violations = [*report.get("violations", []), *report.get("unconnected_items", [])]
    return CheckOutput(_findings("pcb.drc", violations, warning_as="P1"))


def pcb_parity(ctx: Context) -> CheckOutput:
    if not ctx.sch.is_file():
        return CheckOutput(data={"note": "sem .kicad_sch: paridade não avaliada"})
    report = ctx.drc_report()
    return CheckOutput(_findings("pcb.parity", report.get("schematic_parity", []), warning_as="P1"))


# --- S3 / S4 ---------------------------------------------------------------


def _key(v: dict[str, Any]) -> tuple:
    pos = next((i.get("pos") for i in v.get("items", []) if "pos" in i), None)
    return (v.get("type"), v.get("description"), (pos or {}).get("x"), (pos or {}).get("y"))


def fab_rules(ctx: Context) -> CheckOutput:
    """DRC com o perfil JLCPCB numa CÓPIA do snapshot; só conta o que o perfil acrescenta."""
    clone = ctx.snapshot.clone("fab-rules")
    pcb = clone / ctx.pcb.relative_to(ctx.snapshot.root)
    pcb.with_suffix(".kicad_dru").write_text(jlcpcb.rules_text(ctx.fab_profile), encoding="utf-8")
    out = ctx.check_dir("fab.rules") / "drc.json"
    report = kicad_cli.drc_report(pcb, out, parity=False)
    baseline = ctx.drc_report()
    known = {
        _key(v) for v in [*baseline.get("violations", []), *baseline.get("unconnected_items", [])]
    }
    new = [v for v in report.get("violations", []) if _key(v) not in known]
    return CheckOutput(
        _findings("fab.rules", new, warning_as="P1"), {"fab_profile": ctx.fab_profile}
    )


_GERBER_EXT = {
    "F.Cu": ".gtl", "B.Cu": ".gbl", "F.Mask": ".gts", "B.Mask": ".gbs",
    "F.Silkscreen": ".gto", "B.Silkscreen": ".gbo", "Edge.Cuts": ".gm1",
}  # fmt: skip


def _expected_ext(layer: str) -> str:
    inner = re.fullmatch(r"In(\d+)\.Cu", layer)
    return f".g{inner.group(1)}" if inner else _GERBER_EXT[layer]


def fab_gerbers(ctx: Context) -> CheckOutput:
    outdir = ctx.check_dir("fab.gerbers") / "gerbers"
    layers = jlcpcb.fab_layers(ctx.pcb)
    kicad_cli.pcb_export_gerbers(ctx.pcb, outdir, layers=layers)
    kicad_cli.pcb_export_drill(ctx.pcb, outdir)
    present = {p.suffix.lower() for p in outdir.iterdir()}
    missing = [
        (f"camada {layer} sem Gerber ({_expected_ext(layer)})", layer)
        for layer in layers
        if _expected_ext(layer) not in present
    ]
    if ".drl" not in present:
        missing.append(("nenhum arquivo de furação (.drl)", "drill"))
    findings = [
        Finding(
            f"fab.gerbers/{i:03d}",
            "P0",
            title,
            locator={"layer": layer},
            rule="fab-outputs",
            suggested_action="Verificar a camada/furos no PCB Editor e exportar de novo",
        )
        for i, (title, layer) in enumerate(missing, 1)
    ]
    return CheckOutput(
        findings, {"layers": layers, "files": sorted(p.name for p in outdir.iterdir())}
    )


BUILTIN: dict[str, CheckFn] = {
    "intake.tools": intake_tools,
    "intake.files": intake_files,
    "sch.erc": sch_erc,
    "pcb.drc": pcb_drc,
    "pcb.parity": pcb_parity,
    "fab.rules": fab_rules,
    "fab.gerbers": fab_gerbers,
}
