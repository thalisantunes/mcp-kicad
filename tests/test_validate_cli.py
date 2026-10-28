"""CLI `validate` (exit codes) e tool MCP `validate`."""

from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_kicad import __main__ as cli
from mcp_kicad import server
from mcp_kicad.validate import run_validate
from mcp_kicad.validate.checks import CheckOutput
from mcp_kicad.validate.finding import Finding
from mcp_kicad.validate.graph import DEFAULT_CHECKS


def _patch(monkeypatch, fails=None):
    fails = fails or {}
    reg = {
        c.id: (lambda cid: lambda ctx: CheckOutput(list(fails.get(cid, []))))(c.id)
        for c in DEFAULT_CHECKS
    }

    def fake(project, **kw):
        return run_validate(project, checks=reg, **kw)

    monkeypatch.setattr("mcp_kicad.validate.run_validate", fake)
    monkeypatch.setattr(server, "run_validate", fake)


def test_cli_pass_exit_0_and_prints_summary(fake_project, tmp_path, monkeypatch, capsys) -> None:
    _patch(monkeypatch)
    assert cli.main(["validate", str(fake_project), "--out", str(tmp_path / "o")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("PASS") and "REPORT.md" in out


def test_cli_fail_exit_1_json_and_junit(fake_project, tmp_path, monkeypatch, capsys) -> None:
    _patch(monkeypatch, {"sch.erc": [Finding("sch.erc/001", "P0", "ruim")]})
    junit = tmp_path / "j.xml"
    args = ["validate", str(fake_project), "--out", str(tmp_path / "o"), "--json"]
    code = cli.main([*args, "--junit", str(junit), "--profile", "release", "--gate", "full"])
    data = json.loads(capsys.readouterr().out)
    assert code == 1 and data["verdict"] == "FAIL" and junit.is_file()
    assert data["next_actions"][0]["finding_id"] == "sch.erc/001"


def test_cli_incomplete_exit_3_with_csv_lists(fake_project, tmp_path, monkeypatch) -> None:
    _patch(monkeypatch)
    out = str(tmp_path / "o")
    assert cli.main(["validate", str(fake_project), "--out", out, "--only", "S0,sch.erc"]) == 3
    assert cli.main(["validate", str(fake_project), "--out", out, "--until", "S1"]) == 3
    assert cli.main(["validate", str(fake_project), "--out", out, "--skip", "S4,S3"]) == 3


def test_cli_usage_errors_exit_2(fake_project, tmp_path, capsys) -> None:
    assert cli.main(["validate", str(fake_project), "--only", "nope"]) == 2
    assert "MK-VAL-001" in capsys.readouterr().err
    assert cli.main(["validate", str(tmp_path / "x.kicad_pro")]) == 2
    with pytest.raises(SystemExit) as exc:
        cli.main(["validate", str(fake_project), "--profile", "turbo"])
    assert exc.value.code == 2


def test_cli_without_command_serves(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(server, "main", lambda: called.append(1))
    assert cli.main([]) == 0 and cli.main(["serve"]) == 0
    assert called == [1, 1]


def test_tool_returns_verdict_run_dir_and_next_actions(fake_project, tmp_path, monkeypatch):
    _patch(monkeypatch, {"sch.erc": [Finding("sch.erc/001", "P0", "ruim")]})
    out = fake_project.parent / "saida"
    result = server.validate(str(fake_project), out=str(out), only=["S1"])
    assert result["verdict"] == "FAIL" and result["run_dir"].startswith(str(out))
    assert result["next_actions"][0]["finding_id"] == "sch.erc/001"


def test_tool_refuses_out_outside_the_project_but_cli_allows_it(
    fake_project, tmp_path, monkeypatch
) -> None:
    _patch(monkeypatch)
    for bad in (tmp_path / "fora", fake_project.parent / ".." / "fora"):
        with pytest.raises(ToolError, match=r"MK-VAL-001.*dentro do diretório"):
            server.validate(str(fake_project), out=str(bad))
        assert not bad.exists()
    assert cli.main(["validate", str(fake_project), "--out", str(tmp_path / "fora")]) == 0


def test_cli_rerun_hint_preserves_options_and_quotes(
    fake_project, tmp_path, monkeypatch, capsys
) -> None:
    _patch(monkeypatch, {"sch.erc": [Finding("sch.erc/001", "P0", "ruim")]})
    out = tmp_path / "pasta com espaço"
    args = ["validate", str(fake_project), "--out", str(out), "--json", "--until", "S3"]
    args += ["--skip", "fab.gerbers", "--junit", str(tmp_path / "j x.xml")]
    cli.main(args)
    rerun = json.loads(capsys.readouterr().out)["rerun"]
    import shlex

    parts = shlex.split(rerun)
    assert parts[:3] == ["mcp-kicad", "validate", str(fake_project)]
    flags = {parts[i]: parts[i + 1] for i in range(3, len(parts), 2)}
    assert flags["--out"] == str(out) and flags["--junit"] == str(tmp_path / "j x.xml")
    assert flags["--until"] == "S3" and flags["--skip"] == "fab.gerbers"
    assert flags["--only"] == "sch.erc"


def test_tool_turns_config_errors_into_tool_error(fake_project) -> None:
    with pytest.raises(ToolError, match="MK-VAL-001"):
        server.validate(str(fake_project), gate="nope")
