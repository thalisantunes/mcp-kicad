"""Runner: perfis, gate, until/only/skip, exit codes, snapshot — com checks fakes."""

from __future__ import annotations

import json
import os

import pytest

from mcp_kicad.errors import (
    InvalidPath,
    OutputOutsideProject,
    SnapshotMutated,
    ValidateGraphInvalid,
)
from mcp_kicad.validate import run_validate
from mcp_kicad.validate.checks import CheckOutput
from mcp_kicad.validate.config import CONFIG_RELPATH
from mcp_kicad.validate.finding import Finding
from mcp_kicad.validate.graph import DEFAULT_CHECKS
from mcp_kicad.validate.runner import is_blocking

IDS = [c.id for c in DEFAULT_CHECKS]


def finding(check: str, severity: str = "P0", confidence: str = "high", n: int = 1) -> Finding:
    return Finding(f"{check}/{n:03d}", severity, f"problema {n}", confidence=confidence)


def registry(fails: dict[str, list[Finding] | Exception] | None = None, calls: list | None = None):
    fails = fails or {}

    def make(cid):
        def fn(ctx):
            if calls is not None:
                calls.append(cid)
            outcome = fails.get(cid, [])
            if isinstance(outcome, BaseException):
                raise outcome
            return CheckOutput(list(outcome))

        return fn

    return {cid: make(cid) for cid in IDS}


def go(project, tmp_path, fails=None, **kw):
    return run_validate(project, out=tmp_path / "out", checks=registry(fails), **kw)


def statuses(run) -> dict[str, str]:
    return {r.id: r.status for r in run.results}


def test_all_pass(fake_project, tmp_path) -> None:
    run = go(fake_project, tmp_path)
    assert run.verdict == "PASS" and run.exit_code == 0
    assert set(statuses(run).values()) == {"PASS"}
    assert (run.run_dir / "report.json").is_file()


def test_gate_stage_finishes_the_stage_then_stops(fake_project, tmp_path) -> None:
    fails = {"pcb.drc": [finding("pcb.drc")], "pcb.parity": [finding("pcb.parity", "P0")]}
    run = go(fake_project, tmp_path, fails, gate="stage")
    st = statuses(run)
    assert st["pcb.drc"] == st["pcb.parity"] == "FAIL"  # mesmo estágio: ambos rodam
    assert st["fab.rules"] == st["fab.gerbers"] == "SKIPPED"
    assert run.exit_code == 1


def test_gate_stage_stops_before_later_stage_on_erc_failure(fake_project, tmp_path) -> None:
    calls: list[str] = []
    run = run_validate(
        fake_project,
        out=tmp_path / "o",
        checks=registry({"sch.erc": [finding("sch.erc")]}, calls),
    )
    assert "pcb.drc" not in calls and statuses(run)["pcb.drc"] == "SKIPPED"


def test_gate_full_skips_only_dependents(fake_project, tmp_path) -> None:
    # pcb.parity é irmão do drc: não depende dele, então roda em `full`
    run = go(fake_project, tmp_path, {"pcb.drc": [finding("pcb.drc")]}, gate="full")
    st = statuses(run)
    assert st["pcb.parity"] == "PASS"  # irmão do drc roda em `full`
    assert st["fab.rules"] == "SKIPPED" and st["fab.gerbers"] == "SKIPPED"
    reasons = {r.id: r.reason for r in run.results}
    assert reasons["fab.rules"].startswith("blocked")


def test_gate_strict_stops_at_first_failure(fake_project, tmp_path) -> None:
    fails = {"pcb.drc": [finding("pcb.drc")]}
    run = go(fake_project, tmp_path, fails, gate="strict")
    assert statuses(run)["pcb.parity"] == "SKIPPED"
    assert "strict" in {r.id: r.reason for r in run.results}["pcb.parity"]


def test_profiles_dev_vs_release(fake_project, tmp_path) -> None:
    p2 = {"sch.erc": [finding("sch.erc", "P2")]}
    assert go(fake_project, tmp_path, p2, profile="dev").verdict == "PASS"
    assert statuses(go(fake_project, tmp_path, p2, profile="dev"))["sch.erc"] == "WARN"
    assert go(fake_project, tmp_path, p2, profile="release").verdict == "FAIL"
    low = {"sch.erc": [finding("sch.erc", "P0", confidence="low")]}
    assert go(fake_project, tmp_path, low, profile="dev").verdict == "PASS"
    assert go(fake_project, tmp_path, low, profile="release").verdict == "FAIL"


def test_is_blocking_matrix() -> None:
    assert is_blocking(finding("a", "P1", "medium"), "dev")
    assert not is_blocking(finding("a", "P1", "low"), "dev")
    assert not is_blocking(finding("a", "P3", "high"), "release")


def test_warn_gate_never_fails(fake_project, tmp_path, user_config) -> None:
    user_config({"checks": [{"id": "sch.erc", "gate": "warn"}]})  # só o usuário rebaixa builtin
    run = go(fake_project, tmp_path, {"sch.erc": [finding("sch.erc")]})
    assert statuses(run)["sch.erc"] == "WARN" and run.verdict == "PASS"
    assert run.overrides == [{"source": "user", "id": "sch.erc", "gate": "warn"}]
    data = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
    assert data["overrides"] == run.overrides
    assert "Overrides de configuração" in (run.run_dir / "REPORT.md").read_text(encoding="utf-8")


def test_until_only_skip_of_blocking_checks_make_incomplete_exit_3(fake_project, tmp_path) -> None:
    run = go(fake_project, tmp_path, until="S1")
    st = statuses(run)
    assert st["sch.erc"] == "PASS" and st["pcb.drc"] == "SKIPPED"
    assert run.verdict == "INCOMPLETE" and run.exit_code == 3

    run = go(fake_project, tmp_path, until="pcb.drc")
    assert statuses(run)["pcb.drc"] == "PASS" and statuses(run)["pcb.parity"] == "SKIPPED"

    run = go(fake_project, tmp_path, only=["S1"])
    assert [r.id for r in run.results if r.status == "PASS"] == [
        "intake.tools", "intake.files", "sch.erc",
    ]  # fmt: skip
    assert run.exit_code == 3

    run = go(fake_project, tmp_path, skip=["fab.gerbers"])  # skip de check bloqueante: incompleto
    assert statuses(run)["fab.gerbers"] == "SKIPPED" and run.exit_code == 3
    assert run.verdict == "INCOMPLETE"


def test_skipped_check_blocks_its_dependents_with_blocked_by(fake_project, tmp_path) -> None:
    calls: list[str] = []
    run = run_validate(
        fake_project, out=tmp_path / "o", checks=registry(calls=calls), skip=["pcb.drc"]
    )
    by_id = {r.id: r for r in run.results}
    assert by_id["pcb.drc"].reason == "skip" and by_id["pcb.parity"].status == "PASS"
    assert by_id["fab.rules"].status == by_id["fab.gerbers"].status == "SKIPPED"
    assert by_id["fab.rules"].blocked_by == ["pcb.drc"]
    assert by_id["fab.gerbers"].blocked_by == ["fab.rules"]
    assert "fab.rules" not in calls and "fab.gerbers" not in calls  # nunca sem pré-requisito
    assert run.exit_code == 3
    data = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
    assert [r["blocked_by"] for r in data["results"] if r["id"] == "fab.rules"] == [["pcb.drc"]]


def test_until_does_not_run_dependents(fake_project, tmp_path) -> None:
    calls: list[str] = []
    run_validate(fake_project, out=tmp_path / "o", checks=registry(calls=calls), until="S2")
    assert "fab.rules" not in calls and "fab.gerbers" not in calls


def test_only_includes_hard_prerequisites(fake_project, tmp_path) -> None:
    calls: list[str] = []
    run = run_validate(
        fake_project, out=tmp_path / "o", checks=registry(calls=calls), only=["fab.gerbers"]
    )
    assert calls == [
        "intake.tools", "intake.files", "sch.erc", "pcb.drc", "fab.rules", "fab.gerbers",
    ]  # fmt: skip
    st = statuses(run)
    assert st["pcb.parity"] == "SKIPPED" and st["fab.gerbers"] == "PASS"
    assert run.exit_code == 3  # pcb.parity ficou de fora por escolha


def test_failure_wins_over_incomplete(fake_project, tmp_path) -> None:
    run = go(fake_project, tmp_path, {"sch.erc": [finding("sch.erc")]}, until="S2")
    assert run.exit_code == 1


def test_bad_flags_raise_mk_val_001(fake_project, tmp_path) -> None:
    for kw in ({"profile": "x"}, {"gate": "x"}, {"until": "x"}, {"only": ["x"]}, {"skip": ["x"]}):
        with pytest.raises(ValidateGraphInvalid, match="MK-VAL-001"):
            go(fake_project, tmp_path, **kw)


def test_missing_project_is_invalid_path(tmp_path) -> None:
    with pytest.raises(InvalidPath):
        run_validate(tmp_path / "nope.kicad_pro")


def test_unimplemented_check_is_rejected(fake_project, tmp_path, user_config) -> None:
    user_config({"checks": [{"id": "custom.x", "stage": "S1"}]})
    with pytest.raises(ValidateGraphInvalid, match="desconhecido"):
        go(fake_project, tmp_path)


def test_exception_in_check_becomes_error_and_fails(fake_project, tmp_path) -> None:
    run = go(fake_project, tmp_path, {"sch.erc": RuntimeError("boom")})
    res = {r.id: r for r in run.results}["sch.erc"]
    assert res.status == "ERROR" and "RuntimeError: boom" in res.error
    assert run.exit_code == 1


def test_error_in_warn_gate_check_counts_as_warn(fake_project, tmp_path, user_config) -> None:
    user_config({"checks": [{"id": "sch.erc", "gate": "warn"}]})
    run = go(fake_project, tmp_path, {"sch.erc": RuntimeError("plugin caiu")})
    res = {r.id: r for r in run.results}
    assert res["sch.erc"].status == "ERROR" and not res["sch.erc"].failing
    assert run.verdict != "FAIL"
    assert res["pcb.drc"].status == "SKIPPED" and res["pcb.drc"].blocked_by == ["sch.erc"]
    assert run.verdict == "INCOMPLETE"  # o dependente bloqueante não rodou
    assert run.next_actions()["failed_stage"] is None


def test_check_that_mutates_snapshot_is_error_mk_val_002(fake_project, tmp_path) -> None:
    def vandal(ctx):
        ctx.pcb.write_text("alterado")
        return CheckOutput()

    reg = registry()
    reg["sch.erc"] = vandal
    run = run_validate(fake_project, out=tmp_path / "o", checks=reg)
    res = {r.id: r for r in run.results}["sch.erc"]
    assert res.status == "ERROR" and "MK-VAL-002" in res.error
    assert fake_project.with_suffix(".kicad_pcb").read_text() == "x"  # original intacto


def test_mutation_is_detected_even_when_the_check_raises_and_stops_the_run(
    fake_project, tmp_path
) -> None:
    calls: list[str] = []

    def vandal(ctx):
        calls.append("sch.erc")
        ctx.pcb.write_text("alterado")
        raise RuntimeError("depois de sujar")

    reg = registry(calls=calls)
    reg["sch.erc"] = vandal
    run = run_validate(fake_project, out=tmp_path / "o", checks=reg, gate="full")
    by_id = {r.id: r for r in run.results}
    assert by_id["sch.erc"].status == "ERROR" and "MK-VAL-002" in by_id["sch.erc"].error
    assert "depois de sujar" in by_id["sch.erc"].error
    assert calls == ["intake.tools", "intake.files", "sch.erc"]  # snapshot descartado
    assert by_id["pcb.drc"].status == "SKIPPED" and "snapshot alterado" in by_id["pcb.drc"].reason
    assert by_id["pcb.drc"].blocked_by == ["sch.erc"] and run.exit_code == 1


def test_mutation_is_not_downgraded_by_warn_gate(fake_project, tmp_path, user_config) -> None:
    user_config({"checks": [{"id": "sch.erc", "gate": "warn"}]})

    def vandal(ctx):
        ctx.pcb.write_text("alterado")
        return CheckOutput()

    reg = registry()
    reg["sch.erc"] = vandal
    run = run_validate(fake_project, out=tmp_path / "o", checks=reg)
    assert run.verdict == "FAIL"


def test_keyboard_interrupt_writes_partial_report_exit_130(fake_project, tmp_path) -> None:
    run = go(fake_project, tmp_path, {"pcb.drc": KeyboardInterrupt()})
    assert run.exit_code == 130 and run.verdict == "INCOMPLETE"
    assert statuses(run)["fab.gerbers"] == "SKIPPED"
    data = json.loads((run.run_dir / "report.json").read_text())
    assert data["interrupted"] is True


def test_project_directory_is_not_polluted_except_outputs(fake_project, tmp_path) -> None:
    before = sorted(p.name for p in fake_project.parent.iterdir())
    run_validate(fake_project, checks=registry())  # saída padrão dentro do projeto
    after = sorted(p.name for p in fake_project.parent.iterdir())
    assert after == sorted([*before, ".mcp-kicad"])
    assert (fake_project.parent / ".mcp-kicad" / "validate" / "latest").exists()


def _install_reviewer(tmp_path, monkeypatch, body: str, name: str = "revisor-local") -> str:
    """Executável `name` no PATH (só o nome é aceito em `command`)."""
    import stat
    import sys

    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / name
    exe.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return name


def test_agent_check_runs_command_and_gets_builtin_reports(
    fake_project, tmp_path, monkeypatch, user_config
) -> None:
    name = _install_reviewer(
        tmp_path,
        monkeypatch,
        "import sys, json\n"
        "ctx = sys.stdin.read()\n"
        "assert 'problema 1' in ctx\n"
        'f = {"severity": "P1", "title": "revisor achou", "confidence": "medium"}\n'
        'print("ok\\n```json\\n" + json.dumps({"findings": [f]}) + "\\n```")\n',
    )
    user_config(
        {"checks": [{"id": "review.power", "stage": "S5", "kind": "agent", "gate": "warn",
                     "command": [name]}]}
    )  # fmt: skip
    run = go(fake_project, tmp_path, {"sch.erc": [finding("sch.erc", "P3")]})
    res = {r.id: r for r in run.results}["review.power"]
    assert res.status == "WARN" and res.findings[0].source == "agent"
    assert res.findings[0].id == "review.power/001"


def test_project_config_with_command_is_refused_and_never_executed(
    fake_project, tmp_path, monkeypatch
) -> None:
    marker = tmp_path / "executou"
    name = _install_reviewer(tmp_path, monkeypatch, f"open({str(marker)!r}, 'w').close()\n")
    cfg = fake_project.parent / CONFIG_RELPATH
    cfg.parent.mkdir()
    entry = {"id": "review.x", "stage": "S5", "kind": "agent", "command": [name]}
    cfg.write_text(json.dumps({"checks": [entry]}))
    with pytest.raises(ValidateGraphInvalid, match=r"MK-VAL-001.*command"):
        go(fake_project, tmp_path)
    assert not marker.exists()


def test_project_config_cannot_disable_or_downgrade_builtin_checks(fake_project, tmp_path) -> None:
    cfg = fake_project.parent / CONFIG_RELPATH
    cfg.parent.mkdir()
    for entry in ({"id": "pcb.drc", "enabled": False}, {"id": "sch.erc", "gate": "warn"}):
        cfg.write_text(json.dumps({"checks": [entry]}))
        with pytest.raises(ValidateGraphInvalid, match=r"MK-VAL-001.*builtin"):
            go(fake_project, tmp_path)


def test_project_config_defaults_fab_profile_until_skip_and_are_recorded(
    fake_project, tmp_path
) -> None:
    cfg = fake_project.parent / CONFIG_RELPATH
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"fab_profile": "4layer", "until": "S2", "skip": ["pcb.parity"]}))
    seen = {}

    def spy(ctx):
        seen["profile"] = ctx.fab_profile
        return CheckOutput()

    reg = registry()
    reg["sch.erc"] = spy
    run = run_validate(fake_project, out=tmp_path / "o", checks=reg)
    st = statuses(run)
    assert seen["profile"] == "4layer"
    assert st["pcb.drc"] == "PASS" and st["pcb.parity"] == st["fab.rules"] == "SKIPPED"
    assert {k for o in run.overrides for k in o} >= {"fab_profile", "until", "skip"}
    # a linha de comando vence a config do projeto
    run = run_validate(fake_project, out=tmp_path / "o", checks=reg, until="S1", skip=[])
    assert statuses(run)["pcb.drc"] == "SKIPPED"


def test_restrict_out_confines_output_to_the_project(fake_project, tmp_path) -> None:
    inside = fake_project.parent / ".mcp-kicad" / "saida"
    run = run_validate(fake_project, out=inside, restrict_out=True, checks=registry())
    assert run.run_dir.is_relative_to(inside.resolve())
    for bad in (tmp_path / "fora", fake_project.parent / ".." / "fora", "/tmp"):
        with pytest.raises(OutputOutsideProject, match=r"MK-VAL-004.*dentro de"):
            run_validate(fake_project, out=bad, restrict_out=True, checks=registry())
    run_validate(fake_project, out=tmp_path / "livre", checks=registry())  # CLI: livre


def test_symlinked_out_escaping_the_project_is_refused(fake_project, tmp_path) -> None:
    outside = tmp_path / "alvo"
    outside.mkdir()
    (fake_project.parent / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OutputOutsideProject, match="MK-VAL-004"):
        run_validate(
            fake_project, out=fake_project.parent / "link" / "x", restrict_out=True,
            checks=registry(),
        )  # fmt: skip
    assert not any(outside.iterdir())


def test_symlink_to_outside_file_is_refused_mk_val_002(fake_project, tmp_path) -> None:
    secret = tmp_path / "segredo.txt"
    secret.write_text("senha")
    (fake_project.parent / "leak.txt").symlink_to(secret)
    with pytest.raises(SnapshotMutated, match=r"MK-VAL-002.*fora do projeto"):
        go(fake_project, tmp_path)


def test_check_that_plants_a_symlink_outside_is_a_mutation_and_blocks_the_run(
    fake_project, tmp_path
) -> None:
    calls: list[str] = []

    def planter(ctx):
        (ctx.snapshot.root / "ln").symlink_to(tmp_path)
        return CheckOutput()

    reg = registry(calls=calls)
    reg["sch.erc"] = planter
    run = run_validate(fake_project, out=tmp_path / "o", checks=reg, gate="full")
    by_id = {r.id: r for r in run.results}
    assert by_id["sch.erc"].status == "ERROR" and "MK-VAL-002" in by_id["sch.erc"].error
    assert by_id["pcb.drc"].status == "SKIPPED" and "snapshot alterado" in by_id["pcb.drc"].reason
    assert "pcb.drc" not in calls and run.exit_code == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignora permissões")
def test_unreadable_file_after_check_is_a_mutation_error(fake_project, tmp_path) -> None:
    def locker(ctx):
        ctx.pcb.chmod(0)
        return CheckOutput()

    reg = registry()
    reg["sch.erc"] = locker
    run = run_validate(fake_project, out=tmp_path / "o", checks=reg, gate="full")
    by_id = {r.id: r for r in run.results}
    assert by_id["sch.erc"].status == "ERROR" and "MK-VAL-002" in by_id["sch.erc"].error
    assert by_id["pcb.drc"].status == "SKIPPED" and by_id["pcb.drc"].blocked_by == ["sch.erc"]
    assert run.exit_code == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignora permissões")
def test_unreadable_file_before_check_skips_the_check_and_taints(fake_project, tmp_path) -> None:
    calls: list[str] = []

    def locker(ctx):  # roda em intake.files; o próximo check já encontra o arquivo ilegível
        ctx.pcb.chmod(0)
        return CheckOutput()

    reg = registry(calls=calls)
    reg["intake.files"] = locker
    run = run_validate(fake_project, out=tmp_path / "o", checks=reg, gate="full")
    by_id = {r.id: r for r in run.results}
    assert by_id["intake.files"].status == "ERROR"
    assert "sch.erc" not in calls and by_id["sch.erc"].status == "SKIPPED"


def test_symlinked_default_base_is_refused_when_restricted(fake_project, tmp_path) -> None:
    outside = tmp_path / "alvo"
    outside.mkdir()
    (fake_project.parent / ".mcp-kicad").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OutputOutsideProject, match="MK-VAL-004"):
        run_validate(fake_project, restrict_out=True, checks=registry())
    assert not any(outside.iterdir())


def test_symlinked_runs_directory_is_refused(fake_project, tmp_path) -> None:
    outside = tmp_path / "alvo"
    outside.mkdir()
    base = tmp_path / "out"
    base.mkdir()
    (base / "runs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OutputOutsideProject, match="MK-VAL-004"):
        run_validate(fake_project, out=base, checks=registry())
    assert not any(outside.iterdir())


def test_restrict_out_requires_mcp_kicad_dir_and_refuses_root_and_git(
    fake_project, monkeypatch
) -> None:
    root = fake_project.parent
    monkeypatch.chdir(root)
    for bad in (".", root, root / "saida", root / ".git" / "x", root / ".mcp-kicad" / ".git"):
        with pytest.raises(OutputOutsideProject, match="MK-VAL-004"):
            run_validate(fake_project, out=bad, restrict_out=True, checks=registry())
    assert not (root / ".git").exists() and not (root / "saida").exists()


def test_existing_latest_file_or_dir_is_preserved_with_warning(fake_project, tmp_path) -> None:
    for kind in ("file", "dir"):
        out = tmp_path / kind
        out.mkdir()
        latest = out / "latest"
        latest.write_text("meu") if kind == "file" else (latest.mkdir(), (latest / "x").touch())
        run = run_validate(fake_project, out=out, checks=registry())
        assert not latest.is_symlink() and any("não atualizado" in w for w in run.warnings)
        assert (latest.read_text() == "meu") if kind == "file" else (latest / "x").exists()


def test_default_out_refuses_planted_symlinks_without_restrict_out(fake_project, tmp_path):
    outside = tmp_path / "alvo"
    outside.mkdir()
    mk = fake_project.parent / ".mcp-kicad"
    mk.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OutputOutsideProject, match="MK-VAL-004"):
        run_validate(fake_project, checks=registry())
    mk.unlink()
    mk.mkdir()
    (mk / "validate").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OutputOutsideProject, match="MK-VAL-004"):
        run_validate(fake_project, checks=registry())
    assert not any(outside.iterdir())
