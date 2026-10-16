"""Testes de src/mcp_kicad/kicad_cli.py sem KiCad instalado.

`resolve_cli`, `subprocess.run` e `run` são substituídos: aqui só se exercita
a lógica do wrapper (descoberta do binário, mensagens de erro, argumentos e
resumo dos relatórios). O comportamento contra o kicad-cli real fica em
test_integration_kicad.py.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from mcp_kicad import kicad_cli
from mcp_kicad.errors import InvalidPath, KicadCliFailed, KicadCliNotFound

# --- resolve_cli -------------------------------------------------------------


def _no_cli_anywhere(monkeypatch: pytest.MonkeyPatch, known: list[str]) -> None:
    """Sem MCP_KICAD_CLI, sem kicad-cli e sem flatpak no PATH; só `known` conta."""
    monkeypatch.delenv("MCP_KICAD_CLI", raising=False)
    monkeypatch.setattr(kicad_cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(kicad_cli, "_KNOWN_CLI", known)


def _fake_binary(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def test_resolve_cli_override_path_with_spaces_unquoted(tmp_path, monkeypatch) -> None:
    binary = _fake_binary(tmp_path / "KiCad 10" / "kicad-cli")
    monkeypatch.setenv("MCP_KICAD_CLI", str(binary))

    assert kicad_cli.resolve_cli() == [str(binary)]


def test_resolve_cli_override_quoted_path_with_spaces(tmp_path, monkeypatch) -> None:
    binary = _fake_binary(tmp_path / "KiCad 10" / "kicad-cli")
    monkeypatch.setenv("MCP_KICAD_CLI", f'"{binary}" --verbose')

    assert kicad_cli.resolve_cli() == [str(binary), "--verbose"]


def test_resolve_cli_override_multi_token_command(monkeypatch) -> None:
    monkeypatch.setenv("MCP_KICAD_CLI", "flatpak run --command=kicad-cli org.kicad.KiCad")

    assert kicad_cli.resolve_cli() == [
        "flatpak",
        "run",
        "--command=kicad-cli",
        "org.kicad.KiCad",
    ]


def test_split_command_windows_keeps_backslashes_and_drops_quotes() -> None:
    command = r'"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe" --verbose'

    assert kicad_cli._split_command(command, posix=False) == [
        r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
        "--verbose",
    ]


def test_known_locations_start_with_the_macos_app_bundle() -> None:
    assert kicad_cli._KNOWN_CLI[0] == "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"


def test_resolve_cli_finds_known_install_location(tmp_path, monkeypatch) -> None:
    binary = _fake_binary(tmp_path / "KiCad.app" / "Contents" / "MacOS" / "kicad-cli")
    _no_cli_anywhere(monkeypatch, known=["/nonexistent/kicad-cli", str(binary)])

    assert kicad_cli.resolve_cli() == [str(binary)]


def test_resolve_cli_known_location_expands_home(tmp_path, monkeypatch) -> None:
    binary = _fake_binary(tmp_path / "Applications" / "KiCad" / "kicad-cli")
    monkeypatch.setenv("HOME", str(tmp_path))
    _no_cli_anywhere(monkeypatch, known=["~/Applications/KiCad/kicad-cli"])

    assert kicad_cli.resolve_cli() == [str(binary)]


def test_resolve_cli_not_found_lists_places_and_gives_example(monkeypatch) -> None:
    _no_cli_anywhere(monkeypatch, known=["/nada/KiCad.app/Contents/MacOS/kicad-cli"])

    with pytest.raises(KicadCliNotFound) as exc_info:
        kicad_cli.resolve_cli()

    message = str(exc_info.value)
    assert "/nada/KiCad.app/Contents/MacOS/kicad-cli" in message
    assert "PATH" in message
    assert "Flatpak" in message
    assert "MCP_KICAD_CLI=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli" in message


def test_resolve_cli_unbalanced_quotes_name_the_variable(monkeypatch) -> None:
    monkeypatch.setenv("MCP_KICAD_CLI", '"/Applications/KiCad 10/kicad-cli')

    with pytest.raises(KicadCliNotFound) as exc_info:
        kicad_cli.resolve_cli()

    assert "MCP_KICAD_CLI" in str(exc_info.value)


def _only_flatpak_on_path(monkeypatch) -> None:
    monkeypatch.delenv("MCP_KICAD_CLI", raising=False)
    monkeypatch.setattr(
        kicad_cli.shutil, "which", lambda name: "/usr/bin/flatpak" if name == "flatpak" else None
    )
    monkeypatch.setattr(kicad_cli, "_KNOWN_CLI", [])


def test_resolve_cli_flatpak_probe_is_isolated_and_bounded(monkeypatch) -> None:
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    _only_flatpak_on_path(monkeypatch)
    monkeypatch.setattr(kicad_cli.subprocess, "run", fake_run)

    assert kicad_cli.resolve_cli() == list(kicad_cli._FLATPAK_CMD)
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["encoding"] == "utf-8"
    assert seen["errors"] == "replace"
    assert seen["timeout"] == 10


def test_resolve_cli_flatpak_probe_timeout_means_not_installed(monkeypatch) -> None:
    def hanging_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    _only_flatpak_on_path(monkeypatch)
    monkeypatch.setattr(kicad_cli.subprocess, "run", hanging_run)

    with pytest.raises(KicadCliNotFound):
        kicad_cli.resolve_cli()


# --- run ---------------------------------------------------------------------


def _fake_subprocess(monkeypatch, *, returncode: int, stderr: str = "", seen: dict | None = None):
    def fake_run(cmd, **kwargs):
        if seen is not None:
            seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, returncode, "", stderr)

    monkeypatch.setattr(kicad_cli, "resolve_cli", lambda: ["kicad-cli"])
    monkeypatch.setattr(kicad_cli.subprocess, "run", fake_run)


def test_run_converts_missing_binary_to_cli_not_found(monkeypatch) -> None:
    monkeypatch.setattr(kicad_cli, "resolve_cli", lambda: ["/nonexistent/dir/kicad-cli"])

    with pytest.raises(KicadCliNotFound) as exc_info:
        kicad_cli.run(["version"])

    assert "/nonexistent/dir/kicad-cli" in str(exc_info.value)
    assert "MCP_KICAD_CLI" in str(exc_info.value)


def test_run_isolates_stdin_and_forces_utf8(monkeypatch) -> None:
    seen: dict = {}
    _fake_subprocess(monkeypatch, returncode=0, seen=seen)

    kicad_cli.run(["version"])

    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["encoding"] == "utf-8"
    assert seen["errors"] == "replace"


def test_run_failure_message_carries_last_15_stderr_lines(monkeypatch) -> None:
    stderr = "\n".join(f"linha {n}" for n in range(1, 31))
    _fake_subprocess(monkeypatch, returncode=3, stderr=stderr)

    with pytest.raises(KicadCliFailed) as exc_info:
        kicad_cli.run(["pcb", "export", "drill", "x.kicad_pcb"])

    message = str(exc_info.value)
    assert "(3)" in message
    assert "linha 30" in message
    assert "linha 16" in message
    assert "linha 15" not in message
    assert exc_info.value.stderr == stderr  # o atributo guarda o stderr completo


# --- argumentos dos exports --------------------------------------------------


def _capture_run(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return kicad_cli.CliResult(0, "", "")

    monkeypatch.setattr(kicad_cli, "run", fake_run)
    return calls


def _board(tmp_path: Path) -> Path:
    board = tmp_path / "board.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    return board


def test_pcb_export_pos_uses_absolute_origin_like_gerber_and_drill(tmp_path, monkeypatch) -> None:
    calls = _capture_run(monkeypatch)

    kicad_cli.pcb_export_pos(_board(tmp_path), tmp_path / "pos.csv")

    assert "--use-drill-file-origin" not in calls[0]


def test_pcb_export_step_passes_subst_models_by_default(tmp_path, monkeypatch) -> None:
    calls = _capture_run(monkeypatch)
    board = _board(tmp_path)
    out = tmp_path / "sub" / "board.step"

    result = kicad_cli.pcb_export_step(board, out)

    args = calls[0]
    assert args[:3] == ["pcb", "export", "step"]
    assert "--subst-models" in args
    assert args[args.index("--output") + 1] == str(out)
    assert args[-1] == str(board)
    assert result == out
    assert out.parent.is_dir()


def test_pcb_export_step_overwrites_an_existing_output(tmp_path, monkeypatch) -> None:
    calls = _capture_run(monkeypatch)

    kicad_cli.pcb_export_step(_board(tmp_path), tmp_path / "board.step")

    assert "--force" in calls[0]


def test_pcb_export_step_can_skip_subst_models(tmp_path, monkeypatch) -> None:
    calls = _capture_run(monkeypatch)

    kicad_cli.pcb_export_step(_board(tmp_path), tmp_path / "board.step", subst_models=False)

    assert "--subst-models" not in calls[0]


# --- DRC / ERC: resumo, relatório fora do projeto, erro com stderr -----------


def _fake_report_run(monkeypatch, report: dict, *, returncode: int = 5, stderr: str = ""):
    """`run` falso que grava `report` no caminho de --output, como o kicad-cli faz."""
    seen: dict[str, Path] = {}

    def fake_run(args, **kwargs):
        out = Path(args[args.index("--output") + 1])
        seen["out"] = out
        out.write_text(json.dumps(report), encoding="utf-8")
        return kicad_cli.CliResult(returncode, "", stderr)

    monkeypatch.setattr(kicad_cli, "run", fake_run)
    return seen


def _violation(kind: str, severity: str, n: int) -> dict:
    return {
        "type": kind,
        "severity": severity,
        "description": f"desc {n}",
        "items": [{"description": "item", "pos": {"x": n, "y": -n}, "uuid": "u"}],
    }


def test_pcb_drc_returns_summary_with_errors_first(tmp_path, monkeypatch) -> None:
    violations = [_violation("clearance", "warning", n) for n in range(1, 24)]
    violations += [_violation("track_width", "error", 99), _violation("track_width", "error", 98)]
    report = {
        "coordinate_units": "mm",
        "violations": violations,
        "unconnected_items": [{"description": "x"}],
        "schematic_parity": [],
    }
    seen = _fake_report_run(monkeypatch, report)

    summary = kicad_cli.pcb_drc(_board(tmp_path), all_severities=True)

    assert summary["violations"] == 25
    assert summary["by_severity"] == {"warning": 23, "error": 2}
    assert len(summary["first"]) == 20
    # erros vêm antes de warnings, senão os 20 primeiros esconderiam os erros
    assert summary["first"][0] == {
        "type": "track_width",
        "severity": "error",
        "description": "desc 99",
        "pos": {"x": 99, "y": -99},
    }
    assert summary["first"][1]["description"] == "desc 98"
    assert summary["unconnected_items"] == 1
    assert summary["schematic_parity"] == 0
    assert summary["coordinate_units"] == "mm"
    assert summary["report_path"] == str(seen["out"])


def test_pcb_drc_report_goes_to_temp_dir_not_next_to_the_board(tmp_path, monkeypatch) -> None:
    board = _board(tmp_path)
    _fake_report_run(monkeypatch, {"violations": []})

    summary = kicad_cli.pcb_drc(board)

    report = Path(summary["report_path"])
    assert report.is_file()
    assert board.parent not in report.parents
    assert list(tmp_path.glob("*.json")) == []


def test_pcb_drc_honors_report_path(tmp_path, monkeypatch) -> None:
    target = tmp_path / "saida" / "relatorio.json"
    _fake_report_run(monkeypatch, {"violations": []})

    summary = kicad_cli.pcb_drc(_board(tmp_path), report_path=target)

    assert summary["report_path"] == str(target)
    assert target.is_file()


def test_pcb_drc_failure_carries_returncode_and_stderr(tmp_path, monkeypatch) -> None:
    _fake_report_run(monkeypatch, {}, returncode=3, stderr="Failed to load board: boom")

    with pytest.raises(KicadCliFailed) as exc_info:
        kicad_cli.pcb_drc(_board(tmp_path))

    assert "(3)" in str(exc_info.value)
    assert "Failed to load board: boom" in str(exc_info.value)


def test_sch_erc_flattens_violations_of_all_sheets(tmp_path, monkeypatch) -> None:
    schematic = tmp_path / "board.kicad_sch"
    schematic.write_text("(kicad_sch)", encoding="utf-8")
    report = {
        "coordinate_units": "mm",
        "sheets": [
            {"path": "/", "violations": [_violation("pin_not_connected", "error", 1)]},
            {"path": "/Power/", "violations": [_violation("footprint_link_issues", "warning", 2)]},
        ],
    }
    _fake_report_run(monkeypatch, report, returncode=0)

    summary = kicad_cli.sch_erc(schematic, all_severities=True)

    assert summary["violations"] == 2
    assert summary["by_severity"] == {"error": 1, "warning": 1}
    assert [v["sheet"] for v in summary["first"]] == ["/", "/Power/"]
    assert "unconnected_items" not in summary


def test_report_path_must_end_in_json(tmp_path, monkeypatch) -> None:
    calls = _capture_run(monkeypatch)

    with pytest.raises(InvalidPath) as exc_info:
        kicad_cli.pcb_drc(_board(tmp_path), report_path=tmp_path / "relatorio.txt")

    assert ".json" in str(exc_info.value)
    assert calls == []  # o kicad-cli nem chegou a rodar


def test_report_path_cannot_be_the_input_file(tmp_path, monkeypatch) -> None:
    """O kicad-cli sobrescreveria a placa (ou o esquemático) com o relatório."""
    calls = _capture_run(monkeypatch)
    board = _board(tmp_path)
    schematic = tmp_path / "board.kicad_sch"
    schematic.write_text("(kicad_sch)", encoding="utf-8")

    with pytest.raises(InvalidPath):
        kicad_cli.pcb_drc(board, report_path=board)
    with pytest.raises(InvalidPath):
        kicad_cli.sch_erc(schematic, report_path=schematic)

    assert board.read_text(encoding="utf-8") == "(kicad_pcb)"
    assert schematic.read_text(encoding="utf-8") == "(kicad_sch)"
    assert calls == []


def test_report_path_symlink_to_the_input_file_is_rejected(tmp_path, monkeypatch) -> None:
    calls = _capture_run(monkeypatch)
    board = _board(tmp_path)
    link = tmp_path / "relatorio.json"
    try:
        link.symlink_to(board)
    except OSError:
        pytest.skip("sem permissão para criar symlink")

    with pytest.raises(InvalidPath):
        kicad_cli.pcb_drc(board, report_path=link)

    assert board.read_text(encoding="utf-8") == "(kicad_pcb)"
    assert calls == []


@pytest.mark.parametrize("kind", ["drc", "erc"])
def test_failed_run_removes_the_temporary_report_dir(kind, tmp_path, monkeypatch) -> None:
    """Em sucesso o diretório fica (o resumo devolve o caminho); em falha não sobra lixo."""
    source = tmp_path / ("board.kicad_pcb" if kind == "drc" else "board.kicad_sch")
    source.write_text("()", encoding="utf-8")
    check = kicad_cli.pcb_drc if kind == "drc" else kicad_cli.sch_erc

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    created: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def spy(*args, **kwargs):
        path = real_mkdtemp(*args, dir=scratch, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(kicad_cli.tempfile, "mkdtemp", spy)
    _fake_report_run(monkeypatch, {}, returncode=3, stderr="boom")

    with pytest.raises(KicadCliFailed):
        check(source)

    assert len(created) == 1
    assert not Path(created[0]).exists()


def test_failed_run_keeps_the_directory_the_caller_chose(tmp_path, monkeypatch) -> None:
    target = tmp_path / "saida" / "relatorio.json"
    _fake_report_run(monkeypatch, {}, returncode=3, stderr="boom")

    with pytest.raises(KicadCliFailed):
        kicad_cli.pcb_drc(_board(tmp_path), report_path=target)

    assert target.parent.is_dir()
