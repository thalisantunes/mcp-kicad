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
