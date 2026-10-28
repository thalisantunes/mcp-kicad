"""Snapshot: exclusões, hash estável e sensível a mudança."""

from __future__ import annotations

import tempfile

import pytest

from mcp_kicad.errors import SnapshotMutated
from mcp_kicad.validate.snapshot import Snapshot


def _populate(root) -> None:
    (root / "a.kicad_pro").write_text("pro")
    (root / "sub").mkdir()
    (root / "sub" / "b.kicad_sch").write_text("sch")
    (root / "3dmodels").mkdir()
    (root / "3dmodels" / "m.bin").write_text("m")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref")
    (root / ".mcp-kicad" / "validate").mkdir(parents=True)
    (root / ".mcp-kicad" / "validate" / "old.json").write_text("{}")
    for name in ("x.step", "y.STP", "z.wrl"):
        (root / name).write_text("big")


def test_snapshot_excludes_heavy_and_vcs_files(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _populate(src)
    snap = Snapshot.create(src)
    try:
        names = {p.relative_to(snap.root).as_posix() for p in snap.root.rglob("*")}
        assert names == {"a.kicad_pro", "sub", "sub/b.kicad_sch"}
        assert (src / "3dmodels" / "m.bin").exists()  # original intacto
    finally:
        snap.cleanup()
    assert not snap.root.exists()


def test_digest_changes_with_content_but_not_with_volatile_files(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _populate(src)
    snap = Snapshot.create(src)
    try:
        before = snap.digest()
        assert snap.digest() == before
        (snap.root / "a.kicad_prl").write_text("ui state")
        assert snap.digest() == before
        (snap.root / "a.kicad_pro").write_text("changed")
        assert snap.digest() != before
    finally:
        snap.cleanup()


def test_clone_is_independent_of_the_snapshot(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _populate(src)
    snap = Snapshot.create(src)
    try:
        before = snap.digest()
        clone = snap.clone("fab")
        (clone / "a.kicad_dru").write_text("rules")
        assert snap.digest() == before
        assert (clone / "sub" / "b.kicad_sch").exists()
    finally:
        snap.cleanup()


def _src(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.kicad_pro").write_text("pro")
    return src


def test_symlink_to_file_outside_the_project_is_refused_and_scratch_removed(
    tmp_path, monkeypatch
) -> None:
    scratch_root = tmp_path / "tmp"
    scratch_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch_root))
    src = _src(tmp_path)
    secret = tmp_path / "segredo.txt"
    secret.write_text("senha")
    (src / "leak.txt").symlink_to(secret)
    with pytest.raises(SnapshotMutated, match=r"MK-VAL-002.*fora do projeto"):
        Snapshot.create(src)
    assert list(scratch_root.iterdir()) == []  # a cópia parcial não fica para trás


def test_symlink_to_outside_directory_is_refused(tmp_path) -> None:
    src = _src(tmp_path)
    outside = tmp_path / "lib"
    outside.mkdir()
    (src / "lib").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SnapshotMutated, match="MK-VAL-002"):
        Snapshot.create(src)


def test_internal_links_are_copied_as_plain_files_or_skipped(tmp_path) -> None:
    src = _src(tmp_path)
    (src / "alias.kicad_pro").symlink_to(src / "a.kicad_pro")  # arquivo interno
    (src / "dangling").symlink_to(src / "nao-existe")  # quebrado, mas interno
    (src / "sub").mkdir()
    (src / "loop").symlink_to(src, target_is_directory=True)  # ancestral: não pode laçar
    snap = Snapshot.create(src)
    try:
        alias = snap.root / "alias.kicad_pro"
        assert alias.is_file() and not alias.is_symlink() and alias.read_text() == "pro"
        assert not (snap.root / "loop").exists() and not (snap.root / "dangling").exists()
    finally:
        snap.cleanup()


def test_exclude_keeps_previous_outputs_out_of_the_snapshot(tmp_path) -> None:
    src = _src(tmp_path)
    (src / "saida" / "runs").mkdir(parents=True)
    (src / "saida" / "runs" / "r.json").write_text("{}")
    snap = Snapshot.create(src, exclude=[src / "saida"])
    try:
        assert not (snap.root / "saida").exists()
    finally:
        snap.cleanup()


def test_clone_copies_the_snapshot_not_the_original(tmp_path) -> None:
    src = _src(tmp_path)
    snap = Snapshot.create(src)
    try:
        (snap.root / "a.kicad_pro").write_text("mudou no snapshot")
        (src / "a.kicad_pro").write_text("mudou no original")
        clone = snap.clone("c")
        assert (clone / "a.kicad_pro").read_text() == "mudou no snapshot"
    finally:
        snap.cleanup()


@pytest.mark.parametrize("name", ["", ".", "..", "../fora", "a/b", "/abs", "x\\y"])
def test_clone_accepts_only_a_single_name_component(tmp_path, name) -> None:
    src = _src(tmp_path)
    snap = Snapshot.create(src)
    try:
        with pytest.raises(ValueError, match="nome de clone"):
            snap.clone(name)
        assert not (snap._scratch.parent / "fora").exists()
        assert snap.clone("ok").parent == snap._scratch
    finally:
        snap.cleanup()


def test_digest_has_no_structural_collisions(tmp_path) -> None:
    # Formato antigo: caminho\0conteúdo concatenados — estes dois gerariam o mesmo fluxo.
    one, two = tmp_path / "one", tmp_path / "two"
    for d in (one, two):
        d.mkdir()
    (one / "a").write_bytes(b"1")
    (one / "b").write_bytes(b"2")
    (two / "a").write_bytes(b"1b\x002")
    s1, s2 = Snapshot.create(one), Snapshot.create(two)
    try:
        assert s1.digest() != s2.digest()
    finally:
        s1.cleanup()
        s2.cleanup()


def test_digest_ignores_volatile_files_case_insensitively(tmp_path) -> None:
    src = _src(tmp_path)
    snap = Snapshot.create(src)
    try:
        before = snap.digest()
        for name in ("A.KICAD_PRL", "x.LCK", "FP-INFO-CACHE", "fp-info-cache"):
            (snap.root / name).write_text("ruído")
        assert snap.digest() == before
        (snap.root / "novo.kicad_sch").write_text("")  # arquivo vazio novo muda o hash
        assert snap.digest() != before
    finally:
        snap.cleanup()
