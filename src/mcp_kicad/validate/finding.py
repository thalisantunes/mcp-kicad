"""Schema único de achado (ADR 0002) e mapeamento da severidade do KiCad para P0..P3."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

SEVERITIES = ("P0", "P1", "P2", "P3")
CONFIDENCES = ("high", "medium", "low")
SOURCES = ("builtin", "agent")

_LOCATOR_KEYS = {
    "sheet": None,
    "refs": [],
    "nets": [],
    "layer": None,
    "x_mm": None,
    "y_mm": None,
}


def _is_finite_number(val: Any) -> bool:
    if isinstance(val, bool) or not isinstance(val, int | float):
        return False
    try:
        return math.isfinite(val)
    except OverflowError:  # int gigante demais para float
        return False


@dataclass(slots=True)
class Finding:
    id: str
    severity: str
    title: str
    detail: str = ""
    locator: dict[str, Any] = field(default_factory=dict)
    evidence: str = ""
    rule: str = ""
    suggested_action: str = ""
    confidence: str = "high"
    source: str = "builtin"

    def __post_init__(self) -> None:
        for name, value, allowed in (
            ("severity", self.severity, SEVERITIES),
            ("confidence", self.confidence, CONFIDENCES),
            ("source", self.source, SOURCES),
        ):
            if value not in allowed:
                raise ValueError(f"{name} inválido: {value!r} (use {', '.join(allowed)})")
        if not isinstance(self.title, str) or not self.title:
            raise ValueError("title é obrigatório (texto não vazio)")
        for name in ("detail", "evidence", "rule", "suggested_action"):
            value = getattr(self, name)
            if value is None:
                setattr(self, name, "")
            elif not isinstance(value, str):
                raise ValueError(f"{name} deve ser texto")
        self._check_locator()

    def _check_locator(self) -> None:
        loc = self.locator
        for key in ("refs", "nets"):
            val = loc.get(key, [])
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                raise ValueError(f"locator.{key} deve ser lista de textos")
        for key in ("sheet", "layer"):
            if loc.get(key) is not None and not isinstance(loc[key], str):
                raise ValueError(f"locator.{key} deve ser texto ou null")
        for key in ("x_mm", "y_mm"):
            val = loc.get(key)
            if val is not None and not _is_finite_number(val):
                raise ValueError(f"locator.{key} deve ser número finito ou null")

    def to_dict(self) -> dict[str, Any]:
        locator = {k: (list(v) if isinstance(v, list) else v) for k, v in _LOCATOR_KEYS.items()}
        locator.update({k: v for k, v in self.locator.items() if k in _LOCATOR_KEYS})
        return {
            "id": self.id,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "locator": locator,
            "evidence": self.evidence,
            "rule": self.rule,
            "suggested_action": self.suggested_action,
            "confidence": self.confidence,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], **overrides: Any) -> Finding:
        """Cria a partir de um dict do schema; `overrides` (id, source...) vencem o dict."""
        if not isinstance(data, dict):
            raise ValueError(f"achado deve ser objeto, recebido {type(data).__name__}")
        merged = {**data, **overrides}
        locator = merged.get("locator") or {}
        if not isinstance(locator, dict):
            raise ValueError("locator deve ser objeto")
        known = {f for f in cls.__dataclass_fields__}  # noqa: C416
        values = {k: v for k, v in merged.items() if k in known and k != "locator"}
        values.setdefault("id", "")
        return cls(locator=locator, **values)


def map_severity(kicad_severity: str | None, *, warning_as: str = "P1") -> str | None:
    """error→P0; warning→`warning_as` (P1 no DRC, P2 no ERC); exclusion→None (ignorado).

    Severidade desconhecida (versão futura do KiCad) vira P2: não some nem reprova no `dev`.
    """
    if kicad_severity == "error":
        return "P0"
    if kicad_severity == "warning":
        return warning_as
    if kicad_severity in ("exclusion", "ignore"):
        return None
    return "P3" if kicad_severity == "info" else "P2"


_KNOWN_KICAD_SEVERITIES = frozenset({"error", "warning", "exclusion", "ignore", "info"})
_REF_RE = re.compile(r"\b(?:of|Symbol|Footprint)\s+([A-Za-z#]+\d+[\w.-]*)")
_NET_RE = re.compile(r"\[([^\]]+)\]")
_LAYER_RE = re.compile(r"\b((?:F|B|In\d+)\.Cu|Edge\.Cuts|[FB]\.(?:Mask|Silkscreen))\b")


def from_kicad(
    check_id: str,
    index: int,
    violation: dict[str, Any],
    *,
    warning_as: str = "P1",
    sheet: str | None = None,
) -> Finding | None:
    """Converte uma violação do JSON do `kicad-cli` (DRC/ERC); None se excluída/ignorada."""
    if violation.get("excluded"):
        return None
    severity = map_severity(violation.get("severity"), warning_as=warning_as)
    if severity is None:
        return None

    items = violation.get("items", [])
    descriptions = [i.get("description", "") for i in items if i.get("description")]
    text = " ".join(descriptions)
    pos = next((i["pos"] for i in items if isinstance(i.get("pos"), dict)), {})
    layer = _LAYER_RE.search(text)
    kind = violation.get("type", "")
    description = violation.get("description") or kind or "violação"
    return Finding(
        id=f"{check_id}/{index:03d}",
        severity=severity,
        title=description,
        detail=f"Regra `{kind}` do KiCad reportada como {violation.get('severity')}."
        + (
            ""
            if violation.get("severity") in _KNOWN_KICAD_SEVERITIES
            else " Severidade desconhecida: tratada como P2."
        ),
        locator={
            "sheet": sheet,
            "refs": sorted(set(_REF_RE.findall(text))),
            "nets": sorted(set(_NET_RE.findall(text))),
            "layer": layer.group(1) if layer else None,
            "x_mm": pos.get("x"),
            "y_mm": pos.get("y"),
        },
        evidence="; ".join(descriptions),
        rule=kind,
        suggested_action=f"Corrigir a violação '{kind}' e rodar de novo.",
    )
