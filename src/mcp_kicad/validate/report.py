"""Resultados de uma execução e as saídas em disco (ADR 0002, item 6)."""

from __future__ import annotations

import json
import os
import re
import shlex
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .finding import SEVERITIES, Finding
from .graph import STAGE_NAMES, STAGES

SCHEMA = "mcp-kicad.validate/v1"
MAX_ACTIONS = 15
MAX_FINDINGS_PER_CHECK_MD = 50
EXIT_CODES = {"PASS": 0, "FAIL": 1, "INCOMPLETE": 3}
# Caracteres que o XML 1.0 proíbe (controle, surrogates soltos, U+FFFE/FFFF).
_XML_INVALID = re.compile("[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


@dataclass
class CheckResult:
    id: str
    stage: str
    status: str  # PASS | WARN | FAIL | ERROR | SKIPPED
    gate: str = "block"
    source: str = "builtin"
    findings: list[Finding] = field(default_factory=list)
    blocking_ids: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    reason: str = ""  # por que foi pulado
    blocked_by: list[str] = field(default_factory=list)  # pré-requisitos que impediram a execução
    error: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def failing(self) -> bool:
        """Reprova o run: FAIL, ou ERROR de check bloqueante (ERROR com gate warn vale WARN)."""
        return self.status == "FAIL" or (self.status == "ERROR" and self.gate == "block")

    def counts(self) -> dict[str, int]:
        c = Counter(f.severity for f in self.findings)
        return {s: c.get(s, 0) for s in SEVERITIES}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "stage": self.stage,
            "status": self.status,
            "gate": self.gate,
            "source": self.source,
            "duration_s": round(self.duration_s, 3),
            "reason": self.reason,
            "blocked_by": self.blocked_by,
            "error": self.error,
            "counts": self.counts(),
            "data": self.data,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class RunResult:
    project: Path
    project_hash: str
    profile: str
    gate: str
    run_dir: Path
    results: list[CheckResult]
    started_at: str
    duration_s: float
    interrupted: bool = False
    selection: dict[str, Any] = field(default_factory=dict)
    versions: dict[str, Any] = field(default_factory=dict)
    out: str | None = None  # --out/--junit como o chamador passou (para o rerun)
    junit: str | None = None
    overrides: list[dict[str, Any]] = field(default_factory=list)  # config aceita (ADR 0002)
    warnings: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.interrupted:
            return "INCOMPLETE"
        if any(r.failing for r in self.results):
            return "FAIL"
        # check bloqueante que não rodou por escolha (until/only/skip) ou por falta de
        # pré-requisito: o PASS não vale. Pulo por gate só existe junto de uma reprovação.
        if any(
            r.status == "SKIPPED" and r.gate == "block" and not r.reason.startswith("gate")
            for r in self.results
        ):
            return "INCOMPLETE"
        return "PASS"

    @property
    def exit_code(self) -> int:
        return 130 if self.interrupted else EXIT_CODES[self.verdict]

    def counts(self) -> dict[str, int]:
        total: Counter[str] = Counter()
        for r in self.results:
            total.update(r.counts())
        return {s: total.get(s, 0) for s in SEVERITIES}

    def stages(self) -> list[dict[str, str]]:
        out = []
        for s in STAGES:
            rs = [r for r in self.results if r.stage == s]
            if not rs:
                status, why = "SKIPPED", "nenhum check configurado"
                if s == "S5":
                    why = "nenhum plugin de revisão configurado"
            elif any(r.failing for r in rs):
                status, why = "FAIL", ""
            elif all(r.status == "SKIPPED" for r in rs):
                status, why = "SKIPPED", rs[0].reason
            else:
                status, why = "PASS", ""
            out.append({"id": s, "name": STAGE_NAMES[s], "status": status, "reason": why})
        return out

    def first_failed_stage(self) -> str | None:
        bad = [r.stage for r in self.results if r.failing]
        return min(bad, key=STAGES.index) if bad else None

    def rerun_hint(self) -> str:
        stage = self.first_failed_stage()
        ids = [r.id for r in self.results if r.failing and r.stage == stage]
        argv = ["mcp-kicad", "validate", str(self.project)]
        argv += ["--profile", self.profile, "--gate", self.gate]
        if self.selection.get("until"):
            argv += ["--until", self.selection["until"]]
        if ids:
            argv += ["--only", ",".join(ids)]
        if self.selection.get("skip"):
            argv += ["--skip", ",".join(self.selection["skip"])]
        if self.out:
            argv += ["--out", self.out]
        if self.junit:
            argv += ["--junit", self.junit]
        return shlex.join(argv)

    def next_actions(self) -> dict[str, Any]:
        stage = self.first_failed_stage()
        actions: list[dict[str, Any]] = []
        blocked: list[str] = []
        if stage:
            failed = [r for r in self.results if r.stage == stage and r.failing]
            for r in failed:
                if r.error:
                    actions.append(
                        {"check": r.id, "severity": "P0", "title": r.error, "action": "Corrigir"}
                    )
                actions += [
                    {
                        "check": r.id,
                        "finding_id": f.id,
                        "severity": f.severity,
                        "title": f.title,
                        "locator": f.to_dict()["locator"],
                        "action": f.suggested_action,
                    }
                    for f in r.findings
                    if f.id in r.blocking_ids
                ]
            actions.sort(key=lambda a: SEVERITIES.index(a["severity"]))
            blocked = [
                r.id
                for r in self.results
                if r.status == "SKIPPED" and r.reason.startswith(("gate", "blocked"))
            ]
        return {
            "schema": SCHEMA,
            "verdict": self.verdict,
            "failed_stage": stage,
            "actions": actions[:MAX_ACTIONS],
            "actions_total": len(actions),
            "checks_blocked_downstream": blocked,
            "rerun": self.rerun_hint() if stage else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "interrupted": self.interrupted,
            "profile": self.profile,
            "gate": self.gate,
            "selection": self.selection,
            "project": {"path": str(self.project), "hash": self.project_hash},
            "versions": self.versions,
            "overrides": self.overrides,
            "warnings": self.warnings,
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 3),
            "counts": self.counts(),
            "stages": self.stages(),
            "results": [r.to_dict() for r in self.results],
        }

    def summary(self) -> dict[str, Any]:
        """Retorno compacto (tool MCP e `--json`): veredito, caminho do run e next_actions."""
        na = self.next_actions()
        return {
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "profile": self.profile,
            "gate": self.gate,
            "run_dir": str(self.run_dir),
            "report": str(self.run_dir / "report.json"),
            "counts": self.counts(),
            "checks": {r.id: r.status for r in self.results},
            "next_actions": na["actions"],
            "rerun": na["rerun"],
        }


def _md_finding(f: Finding) -> list[str]:
    d = f.to_dict()
    loc = d["locator"]
    where = ", ".join(
        p
        for p in (
            f"folha {loc['sheet']}" if loc["sheet"] else "",
            "refs " + ",".join(map(str, loc["refs"])) if loc["refs"] else "",
            "nets " + ",".join(map(str, loc["nets"])) if loc["nets"] else "",
            f"camada {loc['layer']}" if loc["layer"] else "",
            f"({loc['x_mm']}, {loc['y_mm']}) mm" if loc["x_mm"] is not None else "",
        )
        if p
    )
    return [
        f"- **{f.severity}** `{f.id}` {f.title} ({f.confidence}, {f.source})",
        f"  - Onde: {where or 'n/d'}",
        f"  - Por quê: {f.detail or 'n/d'}",
        f"  - Evidência: {f.evidence or 'n/d'}",
        f"  - Regra: {f.rule or 'n/d'}",
        f"  - Ação: {f.suggested_action or 'n/d'}",
    ]


def render_markdown(run: RunResult) -> str:
    c = run.counts()
    lines = [
        f"# Validate: {run.verdict}",
        "",
        f"- Projeto: `{run.project}` (hash `{run.project_hash[:12]}`)",
        f"- Perfil: {run.profile} · gate: {run.gate} · {run.started_at}",
        "- Achados: " + ", ".join(f"{s}={c[s]}" for s in SEVERITIES),
        *(["- **Interrompido: relatório parcial**"] if run.interrupted else []),
        *(f"- **Aviso:** {w}" for w in run.warnings),
        "",
        "| Estágio | Check | Status | P0 | P1 | P2 | P3 | Nota |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in run.results:
        k = r.counts()
        note = r.error or r.reason
        lines.append(
            f"| {r.stage} | `{r.id}` | {r.status} | {k['P0']} | {k['P1']} | {k['P2']} | {k['P3']}"
            f" | {' '.join(note.split()).replace('|', '/')} |"
        )
    for s in run.stages():
        if s["status"] == "SKIPPED" and s["reason"].startswith("nenhum"):
            lines.append(f"| {s['id']} | (nenhum) | SKIPPED | | | | | {s['reason']} |")
    if run.overrides:
        lines += ["", "## Overrides de configuração aceitos", ""]
        for o in run.overrides:
            lines.append(f"- `{json.dumps(o, ensure_ascii=False)}`")
    for r in run.results:
        if not r.findings:
            continue
        lines += ["", f"## `{r.id}` ({r.status})", ""]
        for f in r.findings[:MAX_FINDINGS_PER_CHECK_MD]:
            lines += _md_finding(f)
        extra = len(r.findings) - MAX_FINDINGS_PER_CHECK_MD
        if extra > 0:
            lines.append(f"- ... e mais {extra} achados em `report.json`")
    return "\n".join(lines) + "\n"


def render_junit(run: RunResult) -> str:
    suite = ET.Element(
        "testsuite",
        name="mcp-kicad.validate",
        tests=str(len(run.results)),
        failures=str(sum(r.status == "FAIL" for r in run.results)),
        errors=str(sum(r.status == "ERROR" for r in run.results)),
        skipped=str(sum(r.status == "SKIPPED" for r in run.results)),
        time=f"{run.duration_s:.3f}",
    )
    for r in run.results:
        case = ET.SubElement(
            suite,
            "testcase",
            classname=f"validate.{r.stage}",
            name=r.id,
            time=f"{r.duration_s:.3f}",
        )
        if r.status == "FAIL":
            body = "\n".join(f"{f.severity} {f.id} {f.title}" for f in r.findings)
            ET.SubElement(
                case, "failure", message=f"{len(r.blocking_ids)} achados bloqueantes"
            ).text = body
        elif r.status == "ERROR":
            ET.SubElement(case, "error", message=r.error)
        elif r.status == "SKIPPED":
            ET.SubElement(case, "skipped", message=r.reason)
    ET.indent(suite)
    xml = ET.tostring(suite, encoding="unicode", xml_declaration=False) + "\n"
    return _XML_INVALID.sub("\ufffd", xml)


def _update_latest(run_dir: Path) -> str | None:
    """Aponta `latest` para o run, de forma atômica. Devolve um aviso se não conseguiu.

    Cria `latest.tmp-<pid>` e troca com `os.replace`: nunca escreve através de um `latest`
    que já exista (um symlink plantado no projeto não redireciona a escrita).
    """
    latest = run_dir.parent.parent / "latest"
    tmp = latest.with_name(f"latest.tmp-{os.getpid()}")
    try:
        if os.path.lexists(latest) and not latest.is_symlink():  # arquivo/diretório do usuário
            raise FileExistsError(f"{latest} existe e não é link simbólico")
        tmp.unlink(missing_ok=True)
        try:
            os.symlink(Path("runs") / run_dir.name, tmp, target_is_directory=True)
        except OSError:  # FS sem symlink: arquivo com o caminho
            tmp.write_text(str(run_dir) + "\n", encoding="utf-8")
        os.replace(tmp, latest)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return f"`latest` não atualizado: {exc}"
    return None


def _write_files(run: RunResult, junit: Path | None) -> None:
    d = run.run_dir
    (d / "report.json").write_text(
        json.dumps(run.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (d / "REPORT.md").write_text(render_markdown(run), encoding="utf-8")
    (d / "next_actions.json").write_text(
        json.dumps(run.next_actions(), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if junit:
        junit.parent.mkdir(parents=True, exist_ok=True)
        junit.write_text(render_junit(run), encoding="utf-8")


def write_outputs(run: RunResult, junit: Path | None = None) -> None:
    run.run_dir.mkdir(parents=True, exist_ok=True)
    _write_files(run, junit)
    warning = _update_latest(run.run_dir)
    if warning:  # o aviso entra no relatório, mas não derruba o run
        run.warnings.append(warning)
        _write_files(run, junit)
