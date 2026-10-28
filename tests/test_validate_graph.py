"""Grafo padrão, ordem topológica, ciclo e override JSON."""

from __future__ import annotations

import json

import pytest

from mcp_kicad.errors import ValidateGraphInvalid
from mcp_kicad.validate.config import (
    CONFIG_RELPATH,
    build_graph,
    load_project_config,
    load_user_config,
)
from mcp_kicad.validate.graph import DEFAULT_CHECKS, STAGES, CheckSpec, Graph


def _ids(graph: Graph) -> list[str]:
    return [c.id for c in graph.order()]


def test_default_order_follows_stages_and_requires() -> None:
    order = _ids(build_graph())
    assert order == [c.id for c in DEFAULT_CHECKS]
    stages = [build_graph().by_id[i].stage for i in order]
    assert stages == sorted(stages, key=STAGES.index)
    assert order.index("sch.erc") < order.index("pcb.drc") < order.index("fab.rules")


def test_cycle_is_rejected_with_mk_val_001() -> None:
    a = CheckSpec("a", "S0", ("b",))
    b = CheckSpec("b", "S0", ("a",))
    with pytest.raises(ValidateGraphInvalid, match=r"MK-VAL-001.*ciclo"):
        Graph([a, b])


def test_unknown_requires_duplicate_and_self_dependency() -> None:
    with pytest.raises(ValidateGraphInvalid, match="não existe"):
        Graph([CheckSpec("a", "S0", ("zz",))])
    with pytest.raises(ValidateGraphInvalid, match="duplicados"):
        Graph([CheckSpec("a", "S0"), CheckSpec("a", "S1")])
    with pytest.raises(ValidateGraphInvalid, match="a si mesmo"):
        Graph([CheckSpec("a", "S0", ("a",))])


@pytest.mark.parametrize(
    "data",
    [
        {"id": "../x", "stage": "S0"},
        {"id": "ok", "stage": "S9"},
        {"id": "ok", "stage": "S0", "gate": "maybe"},
        {"id": "ok", "stage": "S0", "kind": "magic"},
    ],
)
def test_spec_rejects_bad_fields(data) -> None:
    with pytest.raises(ValidateGraphInvalid):
        CheckSpec.from_dict(data)


def test_override_merges_disables_and_adds_agent_check() -> None:
    graph = build_graph(
        {
            "checks": [
                {"id": "pcb.drc", "gate": "warn"},
                {"id": "fab.gerbers", "enabled": False},
                {"id": "review.power", "stage": "S5", "kind": "agent", "command": ["x"]},
            ]
        }
    )
    assert graph.by_id["pcb.drc"].gate == "warn"
    assert "fab.gerbers" not in graph.by_id
    assert graph.by_id["review.power"].options["command"] == ["x"]
    assert _ids(graph)[-1] == "review.power"


def test_disabling_a_check_drops_it_from_requires() -> None:
    graph = build_graph({"checks": [{"id": "pcb.drc", "enabled": False}]})
    assert graph.by_id["fab.rules"].requires == ()


def test_override_errors() -> None:
    with pytest.raises(ValidateGraphInvalid, match="desconhecido"):
        build_graph({"checks": [{"id": "nope", "enabled": False}]})
    with pytest.raises(ValidateGraphInvalid, match="inválida"):
        build_graph({"checks": ["x"]})
    with pytest.raises(ValidateGraphInvalid, match="posterior"):
        build_graph({"checks": [{"id": "intake.tools", "requires": ["fab.gerbers"]}]})
    with pytest.raises(ValidateGraphInvalid, match="ciclo"):
        build_graph({"checks": [{"id": "intake.tools", "requires": ["intake.files"]},
                                {"id": "intake.files", "requires": ["intake.tools"]}]})  # fmt: skip


def test_dependency_on_a_later_stage_is_rejected() -> None:
    with pytest.raises(ValidateGraphInvalid, match=r"MK-VAL-001.*estágio posterior"):
        Graph([CheckSpec("a", "S1", ("b",)), CheckSpec("b", "S3")])
    Graph([CheckSpec("a", "S3", ("b",)), CheckSpec("b", "S1")])  # para trás é permitido


def test_with_prerequisites_is_transitive() -> None:
    graph = build_graph()
    assert graph.with_prerequisites({"fab.rules"}) == {
        "intake.tools", "intake.files", "sch.erc", "pcb.drc", "fab.rules",
    }  # fmt: skip


def test_resolve_accepts_stage_and_check_ids() -> None:
    graph = build_graph()
    assert graph.resolve(["S2", "fab.rules"], flag="only") == {"pcb.drc", "pcb.parity", "fab.rules"}
    with pytest.raises(ValidateGraphInvalid, match="only"):
        graph.resolve(["xx"], flag="only")


def test_load_project_config(tmp_path) -> None:
    assert load_project_config(tmp_path) == {}
    path = tmp_path / CONFIG_RELPATH
    path.parent.mkdir()
    path.write_text(json.dumps({"fab_profile": "4layer"}))
    assert load_project_config(tmp_path)["fab_profile"] == "4layer"
    path.write_text("{nope")
    with pytest.raises(ValidateGraphInvalid, match="ilegível"):
        load_project_config(tmp_path)
    path.write_text("[]")
    with pytest.raises(ValidateGraphInvalid, match="objeto"):
        load_project_config(tmp_path)


def test_yaml_override_gets_a_clear_error(tmp_path) -> None:
    path = (tmp_path / CONFIG_RELPATH).with_suffix(".yaml")
    path.parent.mkdir()
    path.write_text("checks: []")
    with pytest.raises(ValidateGraphInvalid, match="YAML"):
        load_project_config(tmp_path)


# --- confiança: config do projeto x config do usuário -------------------------------------

AGENT = {"id": "review.power", "stage": "S5", "kind": "agent", "command": ["revisor"]}


def _project_cfg(tmp_path, data) -> dict:
    path = tmp_path / CONFIG_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return load_project_config(tmp_path)


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "pcb.drc", "enabled": False},
        {"id": "pcb.drc", "gate": "warn"},
        {"id": "sch.erc", "gate": "block"},
        {"id": "intake.tools", "enabled": False},
        {"id": "fab.rules", "requires": []},
    ],
)
def test_project_config_cannot_touch_builtin_checks(tmp_path, entry) -> None:
    cfg = _project_cfg(tmp_path, {"checks": [entry]})
    with pytest.raises(ValidateGraphInvalid, match=r"MK-VAL-001.*builtin"):
        build_graph({}, cfg)


@pytest.mark.parametrize(
    "entry",
    [
        {**AGENT},
        {"id": "x", "stage": "S5", "command": ["revisor"]},
        {"id": "review.power", "kind": "agent"},
        {"id": "pcb.drc", "command": ["curl", "evil"]},
    ],
)
def test_project_config_cannot_declare_commands_or_agents(tmp_path, entry) -> None:
    cfg = _project_cfg(tmp_path, {"checks": [entry]})
    with pytest.raises(ValidateGraphInvalid, match=r"MK-VAL-001.*command.*usuário"):
        build_graph({"checks": [AGENT]}, cfg)


def test_project_config_cannot_create_checks_or_change_core_fields(tmp_path) -> None:
    cfg = _project_cfg(tmp_path, {"checks": [{"id": "novo", "stage": "S1"}]})
    with pytest.raises(ValidateGraphInvalid, match="não pode criar"):
        build_graph({}, cfg)
    cfg = _project_cfg(tmp_path, {"checks": [{"id": "review.power", "stage": "S1"}]})
    with pytest.raises(ValidateGraphInvalid, match="não permitidas"):
        build_graph({"checks": [AGENT]}, cfg)


def test_project_config_may_adjust_user_defined_checks_and_is_recorded(tmp_path) -> None:
    cfg = _project_cfg(tmp_path, {"checks": [{"id": "review.power", "gate": "warn"}]})
    graph = build_graph({"checks": [AGENT]}, cfg)
    assert graph.by_id["review.power"].gate == "warn"
    assert graph.by_id["review.power"].options["command"] == ["revisor"]
    assert [(o["source"], o["id"]) for o in graph.overrides] == [
        ("user", "review.power"),
        ("project", "review.power"),
    ]
    cfg = _project_cfg(tmp_path, {"checks": [{"id": "review.power", "enabled": False}]})
    assert "review.power" not in build_graph({"checks": [AGENT]}, cfg).by_id


def test_user_config_may_downgrade_builtin(tmp_path) -> None:
    graph = build_graph({"checks": [{"id": "pcb.drc", "gate": "warn"}]})
    assert graph.by_id["pcb.drc"].gate == "warn" and graph.overrides[0]["source"] == "user"


def test_project_config_rejects_unknown_keys_and_bad_values(tmp_path) -> None:
    for data, msg in (
        ({"command": ["x"]}, "não permitidas"),
        ({"fab_profile": "9layer"}, "desconhecido"),
        ({"until": 3}, "until"),
        ({"skip": "S3"}, "skip"),
    ):
        path = tmp_path / CONFIG_RELPATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        with pytest.raises(ValidateGraphInvalid, match=msg):
            load_project_config(tmp_path)


def test_load_user_config_sources(tmp_path, monkeypatch, user_config) -> None:
    assert load_user_config() == {}  # nada configurado
    path = user_config({"checks": [AGENT]})
    assert load_user_config()["checks"][0]["id"] == "review.power"
    path.write_text(json.dumps({"checks": [], "outro": 1}))
    with pytest.raises(ValidateGraphInvalid, match="desconhecidas"):
        load_user_config()
    monkeypatch.setenv("MCP_KICAD_REVIEWERS", str(tmp_path / "nao-existe.json"))
    with pytest.raises(ValidateGraphInvalid, match="inexistente"):
        load_user_config()
