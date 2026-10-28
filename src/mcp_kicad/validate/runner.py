"""Execução do grafo: perfis, gate, until/only/skip e relatório (ADR 0002, itens 3, 5 e 7)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..errors import InvalidPath, McpKicadError, SnapshotMutated, ValidateGraphInvalid
from .checks import BUILTIN, CheckFn, CheckOutput, Context
from .config import build_graph, load_project_config, load_user_config
from .finding import CONFIDENCES, Finding
from .graph import STAGES, CheckSpec, Graph
from .plugins.llm_json import LlmJsonReviewer
from .report import CheckResult, RunResult, write_outputs
from .snapshot import Snapshot

# severidades que reprovam e confiança mínima (item 5 do ADR)
PROFILES: dict[str, tuple[frozenset[str], str]] = {
    "dev": (frozenset({"P0", "P1"}), "medium"),
    "release": (frozenset({"P0", "P1", "P2"}), "low"),
}
GATES = ("stage", "full", "strict")


def is_blocking(finding: Finding, profile: str) -> bool:
    severities, min_conf = PROFILES[profile]
    return finding.severity in severities and (
        CONFIDENCES.index(finding.confidence) <= CONFIDENCES.index(min_conf)
    )


def _agent_check(spec: CheckSpec) -> CheckFn:
    reviewer = LlmJsonReviewer.from_options(spec.id, spec.options)

    def run(ctx: Context) -> CheckOutput:
        reports = {
            cid: [f.to_dict() for f in r.findings][:50]
            for cid, r in ctx.results.items()
            if r.source == "builtin" and r.findings
        }
        context = {"check_id": spec.id, "reports": reports}
        return CheckOutput(reviewer.run(ctx.snapshot.root, context))

    return run


def _registry(graph: Graph, overrides: dict[str, CheckFn] | None) -> dict[str, CheckFn]:
    registry = {**BUILTIN, **(overrides or {})}
    out: dict[str, CheckFn] = {}
    for spec in graph.order():
        if spec.kind == "agent":
            out[spec.id] = _agent_check(spec)
        elif spec.id in registry:
            out[spec.id] = registry[spec.id]
        else:
            raise ValidateGraphInvalid(f"check desconhecido (sem implementação): '{spec.id}'")
    return out


def _selection(graph: Graph, until, only, skip) -> dict[str, str]:
    """id → motivo ("until"/"only"/"skip") dos checks que NÃO devem rodar."""
    order = [c.id for c in graph.order()]
    excluded: dict[str, str] = {}
    if until:
        if until in STAGES:
            last = max((i for i, c in enumerate(graph.order()) if c.stage <= until), default=-1)
        elif until in graph.by_id:
            last = order.index(until)
        else:
            raise ValidateGraphInvalid(f"until: '{until}' não é check nem estágio")
        excluded.update({cid: "until" for cid in order[last + 1 :]})
    if only:
        keep = graph.with_prerequisites(graph.resolve(only, flag="only"))
        excluded.update({cid: "only" for cid in order if cid not in keep})
    if skip:
        excluded.update({cid: "skip" for cid in graph.resolve(skip, flag="skip")})
    return excluded


def _evaluate(spec: CheckSpec, out: CheckOutput, profile: str) -> CheckResult:
    blocking = [f.id for f in out.findings if is_blocking(f, profile)]
    if not out.findings:
        status = "PASS"
    elif blocking and spec.gate == "block":
        status = "FAIL"
    else:
        status = "WARN"
    return CheckResult(
        spec.id, spec.stage, status, spec.gate, spec.kind, out.findings, blocking, data=out.data
    )


def _execute(
    spec: CheckSpec, fn: CheckFn, ctx: Context, profile: str
) -> tuple[CheckResult, bool]:
    """Roda um check. O booleano diz se ele alterou o snapshot (mesmo lançando exceção)."""
    before = ctx.snapshot.digest()
    start = time.monotonic()
    try:
        result = _evaluate(spec, fn(ctx), profile)
    except McpKicadError as exc:
        result = CheckResult(spec.id, spec.stage, "ERROR", spec.gate, spec.kind, error=str(exc))
    except Exception as exc:  # um check quebrado não derruba os outros
        err = f"{type(exc).__name__}: {exc}"
        result = CheckResult(spec.id, spec.stage, "ERROR", spec.gate, spec.kind, error=err)
    mutated = ctx.snapshot.digest() != before
    if mutated:
        msg = str(SnapshotMutated(f"o check '{spec.id}' alterou os arquivos do snapshot"))
        if result.error:
            msg += f" (erro do check: {result.error})"
        # violação de integridade não se rebaixa com `gate: warn`
        result = CheckResult(spec.id, spec.stage, "ERROR", "block", spec.kind, error=msg)
    result.duration_s = time.monotonic() - start
    return result, mutated


def run_validate(
    project: str | Path,
    *,
    profile: str = "dev",
    gate: str = "stage",
    until: str | None = None,
    only: list[str] | None = None,
    skip: list[str] | None = None,
    out: str | Path | None = None,
    junit: str | Path | None = None,
    restrict_out: bool = False,
    checks: dict[str, CheckFn] | None = None,
    graph: Graph | None = None,
    on_result: Callable[[CheckResult], Any] | None = None,
) -> RunResult:
    """Valida um projeto. `checks` substitui implementações por id (testes).

    `restrict_out` (usado pela tool MCP) exige que `out` fique dentro da pasta do projeto.
    """
    if profile not in PROFILES:
        raise ValidateGraphInvalid(f"perfil '{profile}' inválido (use {', '.join(PROFILES)})")
    if gate not in GATES:
        raise ValidateGraphInvalid(f"gate '{gate}' inválido (use {', '.join(GATES)})")
    pro = Path(project).expanduser().resolve()
    if not pro.is_file() or pro.suffix != ".kicad_pro":
        raise InvalidPath(f".kicad_pro não encontrado: {pro}")

    config = load_project_config(pro.parent)
    graph = graph or build_graph(load_user_config(), config)
    overrides = list(graph.overrides)
    if config.get("fab_profile"):
        overrides.append({"source": "project", "fab_profile": config["fab_profile"]})
    if until is None and config.get("until"):
        until = config["until"]
        overrides.append({"source": "project", "until": until})
    if skip is None and config.get("skip"):
        skip = config["skip"]
        overrides.append({"source": "project", "skip": skip})
    registry = _registry(graph, checks)
    excluded = _selection(graph, until, only, skip)
    base = Path(out).expanduser().resolve() if out else pro.parent / ".mcp-kicad" / "validate"
    if out and restrict_out and not base.is_relative_to(pro.parent):
        raise ValidateGraphInvalid(
            f"out deve ficar dentro do diretório do projeto ({pro.parent}): {base}"
        )

    snapshot = Snapshot.create(pro.parent, exclude=[base.resolve()])  # saídas anteriores ficam fora
    try:
        project_hash = snapshot.digest()
        started = datetime.now(UTC)
        run_dir = _new_run_dir(base, started, project_hash)
        ctx = Context(snapshot, snapshot.root / pro.name, run_dir, config)
        t0 = time.monotonic()
        results: list[CheckResult] = []
        interrupted = False
        failed_stage: str | None = None
        failed_strict = False
        tainted: str | None = None  # check que alterou o snapshot: ele não serve mais

        order = graph.order()
        try:
            for spec in order:
                blocked_by: list[str] = []
                if spec.id in excluded:
                    reason = excluded[spec.id]
                elif tainted:
                    reason, blocked_by = f"blocked: snapshot alterado por {tainted}", [tainted]
                else:
                    reason, blocked_by = _gate_reason(
                        spec, gate, failed_stage, failed_strict, ctx.results
                    )
                if reason:
                    res = CheckResult(
                        spec.id,
                        spec.stage,
                        "SKIPPED",
                        spec.gate,
                        spec.kind,
                        reason=reason,
                        blocked_by=blocked_by,
                    )
                else:
                    res, mutated = _execute(spec, registry[spec.id], ctx, profile)
                    if mutated:
                        tainted = spec.id
                    if res.failing:
                        failed_strict = True
                        failed_stage = failed_stage or spec.stage
                results.append(res)
                ctx.results[spec.id] = res
                if on_result:
                    on_result(res)
        except KeyboardInterrupt:
            interrupted = True
            done = {r.id for r in results}
            results += [
                CheckResult(s.id, s.stage, "SKIPPED", s.gate, s.kind, reason="interrupted")
                for s in order
                if s.id not in done
            ]

        run = RunResult(
            project=pro,
            project_hash=project_hash,
            profile=profile,
            gate=gate,
            run_dir=run_dir,
            results=results,
            started_at=started.isoformat(timespec="seconds"),
            duration_s=time.monotonic() - t0,
            interrupted=interrupted,
            selection={"until": until, "only": only, "skip": skip},
            versions={"mcp_kicad": __version__, "kicad_cli": ctx.shared.get("kicad_version")},
            out=str(out) if out else None,
            junit=str(junit) if junit else None,
            overrides=overrides,
        )
        write_outputs(run, Path(junit).expanduser() if junit else None)
        return run
    finally:
        snapshot.cleanup()


def _gate_reason(spec, gate, failed_stage, failed_strict, results) -> tuple[str, list[str]]:
    """Motivo de não rodar o check (vazio = roda) e os pré-requisitos que o impedem."""
    if gate == "strict" and failed_strict:
        return "gate: strict (primeira reprovação)", []
    if gate == "stage" and failed_stage and STAGES.index(spec.stage) > STAGES.index(failed_stage):
        return f"gate: estágio {failed_stage} reprovado", []
    # pré-requisito que reprovou, deu erro ou não rodou (gate, skip, until...): nunca roda sem ele
    missing = [
        dep
        for dep in spec.requires
        if (r := results.get(dep)) and (r.failing or r.status in ("ERROR", "SKIPPED"))
    ]
    if missing:
        return f"blocked: requer {', '.join(missing)}", missing
    return "", []


def _new_run_dir(base: Path, started: datetime, project_hash: str) -> Path:
    name = f"{started:%Y%m%dT%H%M%SZ}-{project_hash[:8]}"
    runs = base / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        candidate = runs / (name if n == 1 else f"{name}-{n}")
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise ValidateGraphInvalid(f"não foi possível criar o diretório do run em {runs}")
