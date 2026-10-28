"""Adaptador genérico: manda o prompt a um comando local e exige um bloco ```json."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

from ...errors import PluginOutputInvalid, ValidateGraphInvalid
from ..finding import Finding

MAX_REPAIRS = 2
_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SCHEMA_HINT = (
    'Responda SOMENTE com um bloco ```json contendo {"findings": [{"severity": "P0|P1|P2|P3", '
    '"title": "...", "detail": "...", "locator": {"sheet": null, "refs": [], "nets": [], '
    '"layer": null, "x_mm": null, "y_mm": null}, "evidence": "...", "rule": "...", '
    '"suggested_action": "...", "confidence": "high|medium|low"}]}. Sem achados: {"findings": []}.'
)


_BASE_ENV = ("PATH", "HOME", "LANG")


def _terminate(proc: subprocess.Popen) -> None:
    """Mata o grupo inteiro (o comando pode ter filhos) e recolhe o processo."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.communicate()


class LlmJsonReviewer:
    def __init__(
        self,
        command: list[str],
        prompt: str,
        timeout_s: int = 600,
        env_passthrough: tuple[str, ...] = (),
    ) -> None:
        self.command = command
        self.prompt = prompt
        self.timeout_s = timeout_s
        self.env_passthrough = env_passthrough

    @classmethod
    def from_options(cls, check_id: str, options: dict[str, Any]) -> LlmJsonReviewer:
        command = options.get("command")
        if not (isinstance(command, list) and command and all(isinstance(c, str) for c in command)):
            raise ValidateGraphInvalid(f"check '{check_id}': 'command' deve ser lista de strings")
        if not command[0] or any(sep in command[0] for sep in ("/", "\\", os.sep)):
            raise ValidateGraphInvalid(
                f"check '{check_id}': command[0] deve ser só o nome de um executável do PATH, "
                f"sem separador de caminho (recebido {command[0]!r})"
            )
        timeout = options.get("timeout_s", 600)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ValidateGraphInvalid(
                f"check '{check_id}': 'timeout_s' deve ser inteiro positivo (recebido {timeout!r})"
            )
        passthrough = options.get("env_passthrough", [])
        if not (isinstance(passthrough, list) and all(isinstance(v, str) for v in passthrough)):
            raise ValidateGraphInvalid(
                f"check '{check_id}': 'env_passthrough' deve ser lista de nomes de variáveis"
            )
        return cls(
            command,
            str(options.get("prompt", "Revise este projeto KiCad.")),
            timeout,
            tuple(passthrough),
        )

    def _env(self) -> dict[str, str]:
        """Ambiente mínimo: o comando não herda segredos que a config do usuário não listou."""
        names = (*_BASE_ENV, *self.env_passthrough)
        return {k: os.environ[k] for k in names if k in os.environ}

    def _ask(self, text: str, cwd: Path) -> str:
        env = self._env()
        exe = shutil.which(self.command[0], path=env.get("PATH"))
        if exe is None:
            raise PluginOutputInvalid(f"comando {self.command[0]!r} não encontrado no PATH")
        try:
            proc = subprocess.Popen(  # noqa: S603
                [exe, *self.command[1:]],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            raise PluginOutputInvalid(f"comando {self.command[0]!r} falhou: {exc}") from exc
        try:
            stdout, stderr = proc.communicate(text, timeout=self.timeout_s)
        except subprocess.TimeoutExpired as exc:
            _terminate(proc)
            raise PluginOutputInvalid(
                f"comando {self.command[0]!r} excedeu {self.timeout_s}s e foi encerrado"
            ) from exc
        except BaseException:  # Ctrl+C: não deixa o grupo órfão
            _terminate(proc)
            raise
        if proc.returncode != 0:
            tail = stderr.strip()[-300:]
            raise PluginOutputInvalid(
                f"comando {self.command[0]!r} saiu com {proc.returncode}: {tail}"
            )
        return stdout

    @staticmethod
    def parse(output: str, check_id: str) -> list[Finding]:
        blocks = _BLOCK_RE.findall(output)
        if not blocks:
            raise ValueError("nenhum bloco ```json encontrado")
        data = json.loads(blocks[-1])
        items = data.get("findings") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ValueError('o JSON precisa ser {"findings": [...]}')
        return [
            Finding.from_dict(item, id=f"{check_id}/{i:03d}", source="agent")
            for i, item in enumerate(items, 1)
        ]

    def run(self, snapshot: Path, context: dict[str, Any]) -> list[Finding]:
        check_id = context["check_id"]
        base = (
            f"{self.prompt}\n\nO projeto está no diretório atual (somente leitura). "
            f"Contexto dos checks builtin (JSON):\n{json.dumps(context, ensure_ascii=False)}\n\n"
            f"{_SCHEMA_HINT}"
        )
        text, last_error = base, ""
        for _ in range(MAX_REPAIRS + 1):
            output = self._ask(text, snapshot)
            try:
                return self.parse(output, check_id)
            except (ValueError, TypeError) as exc:  # JSONDecodeError é ValueError
                last_error = str(exc)
                text = (
                    f"{base}\n\nSua resposta anterior foi rejeitada: {last_error}. "
                    f"Resposta anterior:\n{output[-2000:]}\n\nCorrija e responda de novo."
                )
        raise PluginOutputInvalid(
            f"check '{check_id}': sem bloco JSON válido após {MAX_REPAIRS} reparos ({last_error})"
        )
