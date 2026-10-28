"""Checks builtin com o kicad-cli simulado (sem KiCad)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mcp_kicad.errors import KicadCliNotFound
from mcp_kicad.validate import checks as ck
from mcp_kicad.validate.snapshot import Snapshot

DRC = {
    "violations": [
        {
            "type": "clearance",
            "severity": "error",
            "description": "folga",
            "items": [{"description": "Pad 1 [GND] of U1 on F.Cu", "pos": {"x": 1, "y": 2}}],
        },
        {"type": "silk", "severity": "warning", "description": "silk"},
        {"type": "x", "severity": "exclusion", "description": "ignorado"},
    ],
    "unconnected_items": [{"type": "unconnected_items", "severity": "error", "description": "u"}],
    "schematic_parity": [{"type": "missing_footprint", "severity": "error", "description": "p"}],
}
ERC = {"sheets": [
    {"path": "/", "violations": [{"type": "pin", "severity": "warning", "description": "a"}]},
    {"path": "/s2", "violations": [{"type": "pin", "severity": "error", "description": "b"}]},
]}  # fmt: skip


@pytest.fixture
def ctx(tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    for s in (".kicad_pro", ".kicad_sch", ".kicad_pcb"):
        (src / f"d{s}").write_text("x")
    snap = Snapshot.create(src)
    yield ck.Context(snap, snap.root / "d.kicad_pro", tmp_path / "run")
    snap.cleanup()


@pytest.fixture
def fake_cli(monkeypatch):
    """kicad_cli.run grava o JSON do --output; devolve as chamadas para inspeção."""
    calls: list[list[str]] = []
    payload = {"pcb": DRC, "sch": ERC}

    def run(args, *, check=True, **kw):
        calls.append(args)
        out = Path(args[args.index("--output") + 1])
        out.write_text(json.dumps(payload[args[0]]))
        return ck.kicad_cli.CliResult(5, "", "")

    monkeypatch.setattr(ck.kicad_cli, "run", run)
    return calls


def test_sch_erc_numbers_across_sheets_and_maps_warning_to_p2(ctx, fake_cli) -> None:
    out = ck.sch_erc(ctx)
    assert [(f.id, f.severity, f.locator["sheet"]) for f in out.findings] == [
        ("sch.erc/001", "P2", "/"),
        ("sch.erc/002", "P0", "/s2"),
    ]
    assert (ctx.run_dir / "checks" / "sch.erc" / "erc.json").is_file()


def test_drc_parity_runs_once_and_is_shared(ctx, fake_cli) -> None:
    drc = ck.pcb_drc(ctx)
    parity = ck.pcb_parity(ctx)
    assert [f.severity for f in drc.findings] == ["P0", "P1", "P0"]  # exclusão ignorada
    assert [f.id for f in parity.findings] == ["pcb.parity/001"]
    assert len(fake_cli) == 1 and "--schematic-parity" in fake_cli[0]


def test_parity_without_schematic_is_noop(ctx, fake_cli) -> None:
    ctx.sch.unlink()
    assert ck.pcb_parity(ctx).findings == []
    ck.pcb_drc(ctx)
    assert "--schematic-parity" not in fake_cli[0]


def test_cli_failure_other_than_5_raises(ctx, monkeypatch) -> None:
    monkeypatch.setattr(
        ck.kicad_cli, "run", lambda *a, **k: ck.kicad_cli.CliResult(3, "", "erro feio")
    )
    with pytest.raises(ck.kicad_cli.KicadCliFailed, match="ERC falhou"):
        ck.sch_erc(ctx)


def test_fab_rules_applies_profile_on_a_clone_and_reports_only_new(ctx, fake_cli, monkeypatch):
    seen = {}
    original = ck.kicad_cli.run

    def spy(args, **kw):
        board = Path(args[-1])
        seen.setdefault("boards", []).append(board)
        if "dru" not in seen and board.with_suffix(".kicad_dru").exists():
            seen["dru"] = board.with_suffix(".kicad_dru").read_text()
        return original(args, **kw)

    monkeypatch.setattr(ck.kicad_cli, "run", spy)
    monkeypatch.setattr(ck.jlcpcb, "rules_text", lambda profile: f"(rules {profile})")
    before = ctx.snapshot.digest()

    drc = ck.pcb_drc(ctx)  # baseline
    extra = {"type": "track_width", "severity": "error", "description": "JLC min width"}
    DRC_BAK = list(DRC["violations"])
    DRC["violations"].append(extra)
    try:
        out = ck.fab_rules(ctx)
    finally:
        DRC["violations"][:] = DRC_BAK

    assert drc.findings  # baseline tinha os seus
    assert [f.title for f in out.findings] == ["JLC min width"]
    assert seen["dru"] == "(rules 2layer)"
    assert seen["boards"][-1] != ctx.pcb  # rodou na cópia
    assert ctx.snapshot.digest() == before and not ctx.pcb.with_suffix(".kicad_dru").exists()


def test_intake_tools(ctx, monkeypatch) -> None:
    monkeypatch.setattr(ck.kicad_cli, "version", lambda: "10.0.5")
    out = ck.intake_tools(ctx)
    assert out.findings == [] and ctx.shared["kicad_version"] == "10.0.5"
    monkeypatch.setattr(ck.kicad_cli, "version", lambda: "9.0.1")
    assert ck.intake_tools(ctx).findings[0].severity == "P0"

    def missing():
        raise KicadCliNotFound("sem cli")

    monkeypatch.setattr(ck.kicad_cli, "version", missing)
    assert ck.intake_tools(ctx).findings[0].title == "kicad-cli não encontrado"


def test_intake_files(ctx) -> None:
    assert ck.intake_files(ctx).findings == []
    ctx.pcb.unlink()
    assert [f.title for f in ck.intake_files(ctx).findings] == ["arquivo ausente: d.kicad_pcb"]


def test_fab_gerbers_detects_missing_layers_and_drill(ctx, monkeypatch) -> None:
    layers = ["F.Cu", "In1.Cu", "B.Cu", "Edge.Cuts"]
    monkeypatch.setattr(ck.jlcpcb, "fab_layers", lambda pcb: layers)

    def export(pcb, outdir, **kw):
        Path(outdir).mkdir(parents=True, exist_ok=True)
        for ext in (".gtl", ".gbl", ".gm1"):  # falta In1 (.g1)
            (Path(outdir) / f"d{ext}").write_text("g")
        return outdir

    monkeypatch.setattr(ck.kicad_cli, "pcb_export_gerbers", export)
    monkeypatch.setattr(ck.kicad_cli, "pcb_export_drill", lambda pcb, outdir: outdir)
    titles = [f.title for f in ck.fab_gerbers(ctx).findings]
    assert titles == ["camada In1.Cu sem Gerber (.g1)", "nenhum arquivo de furação (.drl)"]

    monkeypatch.setattr(
        ck.kicad_cli,
        "pcb_export_drill",
        lambda pcb, outdir: (Path(outdir) / "d.drl").write_text("d"),
    )
    shutil.rmtree(ctx.run_dir)
    monkeypatch.setattr(ck.jlcpcb, "fab_layers", lambda pcb: ["F.Cu", "B.Cu", "Edge.Cuts"])
    assert ck.fab_gerbers(ctx).findings == []


def test_fab_rules_compares_severity_and_multiplicity(ctx, fake_cli) -> None:
    item = {"uuid": "u1", "description": "Track", "pos": {"x": 1, "y": 2}}
    base = {"type": "clearance", "severity": "warning", "description": "folga", "items": [item]}
    same_but_error = {**base, "severity": "error"}
    other_item = {**base, "items": [item, {"uuid": "u2", "pos": {"x": 3, "y": 4}}]}
    ctx.shared["drc"] = {"violations": [base]}
    fab = {"violations": [base, base, same_but_error, other_item]}

    def run(args, *, check=True, **kw):
        out = Path(args[args.index("--output") + 1])
        out.write_text(json.dumps(fab))
        return ck.kicad_cli.CliResult(5, "", "")

    ck.kicad_cli.run = run  # restaurado pelo monkeypatch de fake_cli
    out = ck.fab_rules(ctx)
    # 1 igual ao baseline; a 2ª duplicata, a de outra severidade e a de outros itens são novas
    assert len(out.findings) == 3


def test_fab_rules_appends_profile_to_existing_project_rules(ctx, fake_cli, monkeypatch) -> None:
    ctx.pcb.with_suffix(".kicad_dru").write_text("(version 1)\n(rule projeto)\n")
    seen = {}
    original = ck.kicad_cli.run

    def spy(args, **kw):
        dru = Path(args[-1]).with_suffix(".kicad_dru")
        if dru.exists():
            seen["dru"] = dru.read_text()  # a última chamada é a do clone
        return original(args, **kw)

    monkeypatch.setattr(ck.kicad_cli, "run", spy)
    monkeypatch.setattr(ck.jlcpcb, "rules_text", lambda p: "(version 1)\n(rule jlc)\n")
    ck.pcb_drc(ctx)
    ck.fab_rules(ctx)
    assert "(rule projeto)" in seen["dru"] and "(rule jlc)" in seen["dru"]
    assert seen["dru"].count("(version 1)") == 1
    assert ctx.pcb.with_suffix(".kicad_dru").read_text() == "(version 1)\n(rule projeto)\n"
