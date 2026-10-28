"""Schema de achado e mapeamento KiCad → P0..P3."""

from __future__ import annotations

import pytest

from mcp_kicad.validate.finding import Finding, from_kicad, map_severity

VIOLATION = {
    "type": "clearance",
    "severity": "error",
    "description": "Folga insuficiente",
    "items": [
        {"description": "Pad 1 [GND] of U1 on F.Cu", "pos": {"x": 1.5, "y": -2.0}},
        {"description": "Track [+5V] on F.Cu"},
    ],
}


@pytest.mark.parametrize(
    ("kicad", "warning_as", "expected"),
    [
        ("error", "P1", "P0"),
        ("warning", "P1", "P1"),
        ("warning", "P2", "P2"),
        ("exclusion", "P1", None),
        ("ignore", "P1", None),
        ("info", "P1", "P3"),
        ("fatal", "P1", "P2"),
    ],
)
def test_map_severity(kicad, warning_as, expected) -> None:
    assert map_severity(kicad, warning_as=warning_as) == expected


def test_from_kicad_fills_locator_and_schema() -> None:
    f = from_kicad("pcb.drc", 7, VIOLATION, sheet="/")
    assert f is not None
    d = f.to_dict()
    assert d["id"] == "pcb.drc/007"
    assert d["severity"] == "P0"
    assert d["rule"] == "clearance"
    assert d["source"] == "builtin" and d["confidence"] == "high"
    assert d["locator"] == {
        "sheet": "/",
        "refs": ["U1"],
        "nets": ["+5V", "GND"],
        "layer": "F.Cu",
        "x_mm": 1.5,
        "y_mm": -2.0,
    }
    assert "Pad 1 [GND] of U1" in d["evidence"]


def test_from_kicad_ignores_exclusions() -> None:
    assert from_kicad("x", 1, {**VIOLATION, "severity": "exclusion"}) is None
    assert from_kicad("x", 1, {**VIOLATION, "excluded": True}) is None


def test_from_kicad_without_items_keeps_null_locator() -> None:
    f = from_kicad("x", 1, {"type": "t", "severity": "warning"})
    assert f.severity == "P1"
    assert f.to_dict()["locator"]["x_mm"] is None
    assert f.title == "t"


def test_finding_validates_enums_and_title() -> None:
    with pytest.raises(ValueError, match="severity"):
        Finding("a/1", "P9", "t")
    with pytest.raises(ValueError, match="confidence"):
        Finding("a/1", "P0", "t", confidence="sure")
    with pytest.raises(ValueError, match="title"):
        Finding("a/1", "P0", "")


def test_from_dict_roundtrip_and_overrides() -> None:
    f = Finding("a/1", "P2", "t", locator={"refs": ["R1"], "bogus": 1})
    again = Finding.from_dict(f.to_dict(), id="b/2", source="agent")
    assert again.id == "b/2" and again.source == "agent"
    assert again.to_dict()["locator"]["refs"] == ["R1"]
    with pytest.raises(ValueError):
        Finding.from_dict([])
    with pytest.raises(ValueError):
        Finding.from_dict({"severity": "P0", "title": "t", "locator": "x"})


@pytest.mark.parametrize(
    "locator",
    [
        {"refs": [1, 2]},
        {"refs": "R1"},
        {"nets": [None]},
        {"layer": 3},
        {"sheet": ["a"]},
        {"x_mm": "1"},
        {"y_mm": True},
    ],
)
def test_finding_validates_locator_types(locator) -> None:
    with pytest.raises(ValueError, match="locator"):
        Finding("a/1", "P0", "t", locator=locator)


def test_finding_validates_title_type_and_accepts_good_locator() -> None:
    with pytest.raises(ValueError, match="title"):
        Finding("a/1", "P0", 5)  # type: ignore[arg-type]
    Finding("a/1", "P0", "t", locator={"refs": ["R1"], "x_mm": 1, "y_mm": 2.5, "layer": None})


def test_unknown_kicad_severity_becomes_p2_with_note() -> None:
    f = from_kicad("pcb.drc", 1, {"type": "k", "severity": "fatal", "description": "d"})
    assert f is not None and f.severity == "P2" and "desconhecida" in f.detail
    ok = from_kicad("pcb.drc", 1, {"type": "k", "severity": "error", "description": "d"})
    assert ok is not None and "desconhecida" not in ok.detail


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), 10**400])
def test_non_finite_coordinates_are_rejected(bad) -> None:
    for key in ("x_mm", "y_mm"):
        with pytest.raises(ValueError, match="finito"):
            Finding("a/001", "P2", "t", locator={key: bad})


@pytest.mark.parametrize("field", ["detail", "evidence", "rule", "suggested_action"])
def test_text_fields_must_be_str_and_none_becomes_empty(field) -> None:
    assert getattr(Finding.from_dict({"severity": "P2", "title": "t", field: None}), field) == ""
    for bad in (5, ["x"], {"a": 1}):
        with pytest.raises(ValueError, match=field):
            Finding.from_dict({"severity": "P2", "title": "t", field: bad})
