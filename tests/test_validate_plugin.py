"""Adaptador llm_json: bloco JSON, reparos e MK-VAL-003."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mcp_kicad.errors import PluginOutputInvalid, ValidateGraphInvalid
from mcp_kicad.validate.plugins.llm_json import LlmJsonReviewer

GOOD = '```json\n{"findings": [{"severity": "P2", "title": "t"}]}\n```'


def reviewer(tmp_path, body: str, monkeypatch, **kw) -> LlmJsonReviewer:
    """Instala `fake-reviewer` no PATH (`command[0]` só aceita nome de executável)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / "fake-reviewer"
    exe.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return LlmJsonReviewer(["fake-reviewer"], "revise", **kw)


def _alive(pid: int) -> bool:
    for _ in range(30):  # zumbi ainda não recolhido conta como morto
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            return False
        if state == "Z":
            return False
        time.sleep(0.1)
    return True


def test_parse_valid_block_marks_source_agent() -> None:
    findings = LlmJsonReviewer.parse(f"blá\n{GOOD}", "review.x")
    assert [(f.id, f.source, f.severity) for f in findings] == [("review.x/001", "agent", "P2")]


@pytest.mark.parametrize(
    "text", ["sem bloco", "```json\n{nope\n```", '```json\n{"x": 1}\n```', "```json\n[1]\n```"]
)
def test_parse_rejects_invalid(text) -> None:
    with pytest.raises((ValueError, TypeError)):
        LlmJsonReviewer.parse(text, "review.x")


def test_repairs_until_valid(tmp_path, monkeypatch) -> None:
    marker = tmp_path / "n"
    r = reviewer(
        tmp_path,
        f"import pathlib\nm = pathlib.Path({str(marker)!r})\n"
        "n = int(m.read_text()) if m.exists() else 0\nm.write_text(str(n + 1))\n"
        f"print('lixo' if n < 2 else {GOOD!r})\n",
        monkeypatch,
    )
    findings = r.run(tmp_path, {"check_id": "review.x"})
    assert len(findings) == 1 and marker.read_text() == "3"  # 1 tentativa + 2 reparos


def test_gives_up_after_two_repairs(tmp_path, monkeypatch) -> None:
    r = reviewer(tmp_path, "print('sempre lixo')", monkeypatch)
    with pytest.raises(PluginOutputInvalid, match="MK-VAL-003"):
        r.run(tmp_path, {"check_id": "review.x"})


def test_command_failure_is_plugin_error(tmp_path, monkeypatch) -> None:
    r = reviewer(tmp_path, "import sys; sys.stderr.write('falhou'); sys.exit(3)", monkeypatch)
    with pytest.raises(PluginOutputInvalid, match="saiu com 3"):
        r.run(tmp_path, {"check_id": "review.x"})
    missing = LlmJsonReviewer(["comando-que-nao-existe-no-path"], "p")
    with pytest.raises(PluginOutputInvalid, match="não encontrado no PATH"):
        missing.run(tmp_path, {"check_id": "review.x"})


def test_from_options_requires_command() -> None:
    with pytest.raises(ValidateGraphInvalid):
        LlmJsonReviewer.from_options("review.x", {})
    with pytest.raises(ValidateGraphInvalid):
        LlmJsonReviewer.from_options("review.x", {"command": "ollama run x"})
    assert LlmJsonReviewer.from_options("r", {"command": ["a"], "timeout_s": 5}).timeout_s == 5


@pytest.mark.parametrize("command", [["/bin/sh"], ["./x"], ["bin/x"], ["a\\b"], [""]])
def test_command_must_be_a_bare_executable_name(command) -> None:
    with pytest.raises(ValidateGraphInvalid, match=r"MK-VAL-001.*executável"):
        LlmJsonReviewer.from_options("review.x", {"command": command})


@pytest.mark.parametrize("timeout", [0, -5, "10", 1.5, True, None])
def test_timeout_is_validated(timeout) -> None:
    with pytest.raises(ValidateGraphInvalid, match=r"MK-VAL-001.*timeout_s"):
        LlmJsonReviewer.from_options("review.x", {"command": ["a"], "timeout_s": timeout})


def test_env_passthrough_is_validated() -> None:
    for bad in ("TOKEN", [1], {"a": 1}):
        with pytest.raises(ValidateGraphInvalid, match="env_passthrough"):
            LlmJsonReviewer.from_options("r", {"command": ["a"], "env_passthrough": bad})


ENV_DUMP = (
    "import os, json\n"
    'f = {"severity": "P3", "title": json.dumps(sorted(os.environ))}\n'
    'print("```json\\n" + json.dumps({"findings": [f]}) + "\\n```")\n'
)


def test_child_gets_a_minimal_environment_plus_passthrough(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEGREDO_DO_USUARIO", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LANG", "C.UTF-8")
    r = reviewer(tmp_path, ENV_DUMP, monkeypatch)
    seen = json.loads(r.run(tmp_path, {"check_id": "review.x"})[0].title)
    assert set(seen) <= {"PATH", "HOME", "LANG", "LC_CTYPE"}  # LC_CTYPE: o Python pode injetar
    assert "SEGREDO_DO_USUARIO" not in seen and "ANTHROPIC_API_KEY" not in seen

    r = reviewer(tmp_path, ENV_DUMP, monkeypatch, env_passthrough=("ANTHROPIC_API_KEY", "AUSENTE"))
    seen = json.loads(r.run(tmp_path, {"check_id": "review.x"})[0].title)
    assert "ANTHROPIC_API_KEY" in seen and "SEGREDO_DO_USUARIO" not in seen
    assert "AUSENTE" not in seen  # variável que não existe no pai não aparece


def test_io_is_utf_8_regardless_of_locale(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LANG", "C")
    body = (
        "import sys, json\n"
        "ctx = sys.stdin.buffer.read().decode('utf-8')\n"
        "assert 'inspeção' in ctx\n"
        "f = {'severity': 'P3', 'title': 'ação'}\n"
        "out = json.dumps({'findings': [f]}, ensure_ascii=False)\n"
        "sys.stdout.buffer.write(('```json\\n' + out + '\\n```').encode('utf-8'))\n"
    )
    r = reviewer(tmp_path, body, monkeypatch)
    r.prompt = "inspeção"
    assert r.run(tmp_path, {"check_id": "review.x"})[0].title == "ação"


SPAWN_CHILD = (
    "import subprocess, sys, time\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "open({pidfile!r}, 'w').write(str(p.pid))\n"
    "time.sleep(60)\n"
)


def test_timeout_kills_the_whole_process_group(tmp_path, monkeypatch) -> None:
    pidfile = tmp_path / "child.pid"
    r = reviewer(tmp_path, SPAWN_CHILD.format(pidfile=str(pidfile)), monkeypatch, timeout_s=1)
    with pytest.raises(PluginOutputInvalid, match="excedeu 1s"):
        r.run(tmp_path, {"check_id": "review.x"})
    child = int(pidfile.read_text())
    assert not _alive(child)  # o neto também morreu, não só o filho direto


def test_interrupt_kills_the_whole_process_group(tmp_path, monkeypatch) -> None:
    pidfile = tmp_path / "child.pid"
    r = reviewer(tmp_path, SPAWN_CHILD.format(pidfile=str(pidfile)), monkeypatch)
    original = subprocess.Popen.communicate
    calls = []

    def interrupted(self, input=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            for _ in range(50):  # espera o filho publicar o pid do neto
                if pidfile.exists() and pidfile.read_text():
                    break
                time.sleep(0.1)
            raise KeyboardInterrupt
        return original(self, input, timeout)

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        r.run(tmp_path, {"check_id": "review.x"})
    assert not _alive(int(pidfile.read_text()))
