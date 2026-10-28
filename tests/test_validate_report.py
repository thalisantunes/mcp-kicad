"""Saídas em disco: report.json, REPORT.md, next_actions.json, junit.xml, latest."""

from __future__ import annotations

import json
import os
import shlex
import xml.etree.ElementTree as ET

import pytest

from mcp_kicad.validate import run_validate
from mcp_kicad.validate.checks import CheckOutput
from mcp_kicad.validate.finding import Finding
from mcp_kicad.validate.graph import DEFAULT_CHECKS
from mcp_kicad.validate.report import SCHEMA


def _reg(fails):
    def make(cid):
        return lambda ctx: CheckOutput(list(fails.get(cid, [])))

    return {c.id: make(c.id) for c in DEFAULT_CHECKS}


def _f(cid, n, sev):
    return Finding(f"{cid}/{n:03d}", sev, f"t{n}", suggested_action="faça X")


def test_report_files_and_schema(fake_project, tmp_path) -> None:
    junit = tmp_path / "ci" / "junit.xml"
    fails = {"sch.erc": [_f("sch.erc", 1, "P0")], "pcb.drc": [_f("pcb.drc", 1, "P0")]}
    run = run_validate(fake_project, out=tmp_path / "o", junit=junit, checks=_reg(fails))

    d = run.run_dir
    assert d.parent.name == "runs" and d.name.endswith(run.project_hash[:8])
    data = json.loads((d / "report.json").read_text())
    assert data["schema"] == SCHEMA == "mcp-kicad.validate/v1"
    assert data["verdict"] == "FAIL" and data["profile"] == "dev" and data["gate"] == "stage"
    assert data["project"]["hash"] == run.project_hash
    assert data["versions"]["mcp_kicad"]
    assert [s["id"] for s in data["stages"]] == ["S0", "S1", "S2", "S3", "S4", "S5"]
    assert data["stages"][5]["status"] == "SKIPPED"
    assert data["results"][2]["findings"][0]["locator"]["refs"] == []

    md = (d / "REPORT.md").read_text()
    assert "# Validate: FAIL" in md and "| S1 | `sch.erc` | FAIL |" in md
    assert "Onde:" in md and "Ação: faça X" in md and "revisão configurado" in md

    suite = ET.parse(junit).getroot()
    assert suite.get("failures") == "1" and suite.get("tests") == str(len(DEFAULT_CHECKS))
    assert {c.get("name") for c in suite.iter("testcase")} >= {"sch.erc", "pcb.drc"}
    assert len(list(suite.iter("skipped"))) == 4

    latest = tmp_path / "o" / "latest"
    assert latest.resolve() == d.resolve()


def test_next_actions_only_first_failed_stage_capped_and_worst_first(
    fake_project, tmp_path
) -> None:
    many = [_f("sch.erc", i, "P1") for i in range(1, 21)] + [_f("sch.erc", 99, "P0")]
    fails = {"sch.erc": many, "pcb.drc": [_f("pcb.drc", 1, "P0")]}
    run = run_validate(fake_project, out=tmp_path / "o", checks=_reg(fails), gate="full")

    na = json.loads((run.run_dir / "next_actions.json").read_text())
    assert na["failed_stage"] == "S1"
    assert len(na["actions"]) == 15 and na["actions_total"] == 21
    assert na["actions"][0]["severity"] == "P0" and na["actions"][0]["finding_id"] == "sch.erc/099"
    assert {a["check"] for a in na["actions"]} == {"sch.erc"}  # nada de S2
    assert "pcb.drc" in na["checks_blocked_downstream"]
    assert "--only sch.erc" in na["rerun"]


def test_next_actions_empty_on_pass_and_error_becomes_action(fake_project, tmp_path) -> None:
    run = run_validate(fake_project, out=tmp_path / "o", checks=_reg({}))
    na = json.loads((run.run_dir / "next_actions.json").read_text())
    assert na["actions"] == [] and na["failed_stage"] is None and na["rerun"] is None

    reg = _reg({})
    reg["pcb.drc"] = lambda ctx: (_ for _ in ()).throw(ValueError("x"))
    run = run_validate(fake_project, out=tmp_path / "o", checks=reg)
    na = run.next_actions()
    assert na["actions"][0]["title"] == "ValueError: x"


def test_summary_is_compact_and_has_next_actions(fake_project, tmp_path) -> None:
    run = run_validate(
        fake_project, out=tmp_path / "o", checks=_reg({"sch.erc": [_f("sch.erc", 1, "P0")]})
    )
    s = run.summary()
    assert s["verdict"] == "FAIL" and s["run_dir"] == str(run.run_dir)
    assert s["checks"]["sch.erc"] == "FAIL" and s["next_actions"][0]["finding_id"] == "sch.erc/001"
    assert s["counts"]["P0"] == 1


def test_markdown_truncates_long_finding_lists(fake_project, tmp_path) -> None:
    many = [_f("sch.erc", i, "P3") for i in range(1, 80)]
    run = run_validate(fake_project, out=tmp_path / "o", checks=_reg({"sch.erc": many}))
    assert "e mais 29 achados" in (run.run_dir / "REPORT.md").read_text()


def test_latest_falls_back_to_file_when_symlink_fails(fake_project, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("os.symlink", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    run = run_validate(fake_project, out=tmp_path / "o", checks=_reg({}))
    assert (tmp_path / "o" / "latest").read_text().strip() == str(run.run_dir)


def _run(fake_project, tmp_path, fails=None, **kw):
    return run_validate(fake_project, out=tmp_path / "o", checks=_reg(fails or {}), **kw)


def test_latest_never_writes_through_an_existing_symlink(fake_project, tmp_path) -> None:
    victim = tmp_path / "vitima.txt"
    victim.write_text("intacto")
    out = tmp_path / "o"
    out.mkdir()
    (out / "latest").symlink_to(victim)
    (out / f"latest.tmp-{os.getpid()}").symlink_to(victim)  # tmp plantado também não vaza
    run = _run(fake_project, tmp_path)
    assert victim.read_text() == "intacto"
    assert (out / "latest").resolve() == run.run_dir.resolve() and run.warnings == []
    assert not (out / f"latest.tmp-{os.getpid()}").exists()  # nada fica para trás


def test_latest_fallback_file_never_writes_through_a_symlink(
    fake_project, tmp_path, monkeypatch
) -> None:
    victim = tmp_path / "vitima.txt"
    victim.write_text("intacto")
    out = tmp_path / "o"
    out.mkdir()
    (out / "latest").symlink_to(victim)
    monkeypatch.setattr("os.symlink", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    run = _run(fake_project, tmp_path)
    assert victim.read_text() == "intacto"
    assert (out / "latest").read_text().strip() == str(run.run_dir)
    assert not (out / "latest").is_symlink()


def test_latest_as_real_directory_is_a_warning_not_a_crash(fake_project, tmp_path) -> None:
    out = tmp_path / "o"
    (out / "latest").mkdir(parents=True)
    (out / "latest" / "dado.txt").write_text("meu")
    run = _run(fake_project, tmp_path)
    assert run.exit_code == 0 and len(run.warnings) == 1 and "latest" in run.warnings[0]
    assert (out / "latest" / "dado.txt").read_text() == "meu"  # nada apagado
    assert list(out.glob("latest.tmp-*")) == []
    data = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
    assert data["warnings"] == run.warnings
    assert "**Aviso:**" in (run.run_dir / "REPORT.md").read_text(encoding="utf-8")


def test_latest_oserror_is_a_warning_and_leaves_no_tmp(fake_project, tmp_path, monkeypatch) -> None:
    def boom(*a, **k):
        raise PermissionError("somente leitura")

    monkeypatch.setattr("os.replace", boom)
    run = _run(fake_project, tmp_path)
    assert run.verdict == "PASS" and "somente leitura" in run.warnings[0]
    assert not (tmp_path / "o" / "latest").exists()
    assert list((tmp_path / "o").glob("latest.tmp-*")) == []


def test_junit_strips_characters_invalid_in_xml_1_0(fake_project, tmp_path) -> None:
    bad = "ruim\x00\x1b\x08 fim \ufffe ação"
    failing = _reg({"sch.erc": [Finding("sch.erc/001", "P0", bad)]})
    erroring = _reg({})
    erroring["sch.erc"] = lambda ctx: (_ for _ in ()).throw(ValueError(bad))
    for reg, attr in ((failing, "failures"), (erroring, "errors")):
        junit = tmp_path / f"{attr}.xml"
        run_validate(fake_project, out=tmp_path / "o", junit=junit, checks=reg)
        suite = ET.parse(junit).getroot()  # levanta ParseError se sobrar caractere inválido
        text = junit.read_text(encoding="utf-8")
        assert "\x00" not in text and "\x1b" not in text and "ação" in text
        assert suite.get(attr) == "1"


def test_outputs_are_utf_8_encoded(fake_project, tmp_path) -> None:
    fails = {"sch.erc": [Finding("sch.erc/001", "P0", "açúcar não cabe", detail="coração")]}
    run = _run(fake_project, tmp_path, fails, junit=tmp_path / "j.xml")
    for name in ("report.json", "REPORT.md", "next_actions.json"):
        assert "ç".encode() in (run.run_dir / name).read_bytes() or name == "next_actions.json"
    assert "açúcar".encode() in (run.run_dir / "next_actions.json").read_bytes()
    assert "açúcar".encode() in (tmp_path / "j.xml").read_bytes()


def test_error_with_warn_gate_is_reported_as_error_in_junit_but_does_not_fail(
    fake_project, tmp_path, user_config
) -> None:
    user_config({"checks": [{"id": "sch.erc", "gate": "warn"}]})
    reg = _reg({})
    reg["sch.erc"] = lambda ctx: (_ for _ in ()).throw(RuntimeError("plugin caiu"))
    junit = tmp_path / "j.xml"
    run = run_validate(fake_project, out=tmp_path / "o", junit=junit, checks=reg)
    assert run.verdict != "FAIL" and run.exit_code != 1
    assert ET.parse(junit).getroot().get("errors") == "1"


def test_rerun_hint_uses_shell_quoting_and_keeps_selection(fake_project, tmp_path) -> None:
    out = tmp_path / "saída com espaço"
    run = _run(
        fake_project, tmp_path, {"sch.erc": [_f("sch.erc", 1, "P0")]}, until="S3", skip=["S4"]
    )
    run.out, run.junit = str(out), str(tmp_path / "j; rm -rf x.xml")
    hint = run.rerun_hint()
    assert shlex.split(hint)[-1] == run.junit and "'" in hint  # citado, não injetável
    assert "--until S3" in hint and "--skip S4" in hint and "--only sch.erc" in hint


def test_note_cell_collapses_newlines_and_pipes(fake_project, tmp_path) -> None:
    def boom(ctx):
        raise RuntimeError("linha1\nlinha2 | coluna\r\n  fim")

    reg = _reg({})
    reg["sch.erc"] = boom
    run = run_validate(fake_project, out=tmp_path / "o", checks=reg)
    md = (run.run_dir / "REPORT.md").read_text()
    row = next(line for line in md.splitlines() if line.startswith("| S1 | `sch.erc`"))
    assert row.endswith("RuntimeError: linha1 linha2 / coluna fim |")


def test_report_json_rejects_nan(fake_project, tmp_path) -> None:
    from mcp_kicad.validate.report import _write_files

    run = run_validate(fake_project, out=tmp_path / "o", checks=_reg({}))
    run.results[0].data = {"x": float("nan")}
    with pytest.raises(ValueError, match="Out of range"):
        _write_files(run, None)
