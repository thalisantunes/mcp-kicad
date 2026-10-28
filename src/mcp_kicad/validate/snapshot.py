"""Snapshot temporário do projeto: os checks nunca tocam o original (ADR 0002, item 3)."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from ..errors import SnapshotMutated

# `.mcp-kicad/` fica de fora para que as saídas de runs anteriores não entrem na cópia.
EXCLUDED_DIRS = frozenset({"3dmodels", ".git", ".mcp-kicad"})
EXCLUDED_SUFFIXES = frozenset({".step", ".stp", ".wrl"})
# O kicad-cli grava estado local de UI/cache ao abrir o projeto (ex.: .kicad_prl); isso não
# é desenho, então fica fora do hash (gitignore do projeto também ignora). `fp-info-cache` é o
# cache de footprints que o KiCad (re)gera na pasta do projeto ao resolver bibliotecas.
# Comparação em minúsculas: o sistema de arquivos pode ser case-insensitive.
VOLATILE_SUFFIXES = frozenset({".kicad_prl", ".lck"})
VOLATILE_NAMES = frozenset({"fp-info-cache"})


def _ignore_for(root: Path, exclude: frozenset[Path] = frozenset()):
    """`ignore` do copytree: exclui pesados/VCS e RECUSA links que escapem do projeto."""
    root = root.resolve()

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped: set[str] = set()
        for name in names:
            path = Path(directory) / name
            if (
                name in EXCLUDED_DIRS
                or path.suffix.lower() in EXCLUDED_SUFFIXES
                or Path(directory).resolve() / name in exclude
            ) or path.is_symlink() and _check_link(path, root):
                skipped.add(name)
        return skipped

    return ignore


def _check_link(path: Path, root: Path) -> bool:
    """Recusa link para fora da raiz; devolve True se o link deve ficar fora da cópia."""
    try:
        target = path.resolve()
    except (OSError, RuntimeError) as exc:  # laço de links
        raise SnapshotMutated(f"link simbólico não resolvido no projeto: {path} ({exc})") from exc
    if not target.is_relative_to(root):
        raise SnapshotMutated(
            f"link simbólico aponta para fora do projeto: {path} -> {target}. "
            "O validate não copia arquivos de fora da pasta do projeto."
        )
    # link interno para diretório (ex.: o `latest` de saídas antigas) fica fora: copiá-lo
    # duplicaria a árvore, ou entraria em laço se apontar para um ancestral.
    return target.is_dir()


class Snapshot:
    def __init__(self, source: Path, scratch: Path, exclude: Iterable[Path] = ()) -> None:
        self.source = source
        self._scratch = scratch
        self.root = self._copy(source, scratch / "snapshot", frozenset(Path(e) for e in exclude))

    @classmethod
    def create(cls, source: Path, exclude: Iterable[Path] = ()) -> Snapshot:
        """`exclude`: caminhos (ex.: a pasta de saída do run) que não entram na cópia."""
        scratch = Path(tempfile.mkdtemp(prefix="mcp-kicad-snap-"))
        try:
            return cls(source, scratch, exclude)
        except BaseException:
            shutil.rmtree(scratch, ignore_errors=True)
            raise

    @staticmethod
    def _copy(source: Path, dest: Path, exclude: frozenset[Path] = frozenset()) -> Path:
        # symlinks=False: links internos viram arquivos comuns na cópia (nenhum aponta
        # de volta ao original); os que escapam da raiz já foram recusados no `ignore`.
        shutil.copytree(
            source, dest, ignore=_ignore_for(source, exclude),
            symlinks=False,
            ignore_dangling_symlinks=True,
        )
        return dest

    def clone(self, name: str) -> Path:
        """Cópia do SNAPSHOT (não do original) para aplicar, p.ex., um perfil de regras."""
        if not name or name in (".", "..") or name != Path(name).name or "\\" in name:
            raise ValueError(f"nome de clone inválido (use um único componente): {name!r}")
        dest = self._scratch / name
        if dest.resolve().parent != self._scratch.resolve():
            raise ValueError(f"clone fora do scratch: {name!r}")
        return self._copy(self.root, dest)

    def digest(self) -> str:
        """Hash do conteúdo: por arquivo, caminho + tamanho + sha256 (sem ambiguidade de borda)."""
        h = hashlib.sha256()
        for path in sorted(self.root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() in VOLATILE_SUFFIXES or path.name.lower() in VOLATILE_NAMES:
                continue
            file_hash = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    file_hash.update(chunk)
            h.update(path.relative_to(self.root).as_posix().encode())
            h.update(b"\0")
            h.update(str(path.stat().st_size).encode())
            h.update(b"\0")
            h.update(file_hash.digest())
        return h.hexdigest()

    def cleanup(self) -> None:
        shutil.rmtree(self._scratch, ignore_errors=True)
