"""Agent 2 in the running app: network view, table fallback, focus and role filtering."""

from __future__ import annotations

import json

import pytest

from decision_memory.agents.memory_agent import build_decision_graph, layout
from decision_memory.authz import MemoryView
from decision_memory.models import (
    DecisionGraph,
    EdgeType,
    GraphEdge,
    GraphNode,
    NodeKind,
    Role,
)
from decision_memory.repository import Repository
from decision_memory.ui import memory_graph
from decision_memory.ui.memory_graph import (
    FALLBACK_NOTICE,
    FOCUS_KEY,
    KINDS_KEY,
    MODE_KEY,
    build_figure,
    edge_rows,
    node_rows,
)

from conftest import HIDDEN_FROM_ANALYST, HIDDEN_FROM_DIRECTOR, rendered_text


def graph_tables(app) -> str:
    """Only the graph's node and edge tables (not the capture or evidence tables)."""
    return "\n".join(
        frame.value.to_string()
        for frame in app.dataframe
        if {"Node", "Kind"} <= set(frame.value.columns) or "Edge type" in frame.value.columns
    )


SCENARIO_TRACES = {"Revenue change", "Margin change"}
GRAPH_METRICS = {"Decisions", "Outcomes", "Nodes", "Typed edges"}


def chart_specs(app) -> list[str]:
    """The graph's Plotly specs (the Scenarios tab draws a chart of its own)."""
    specs = [chart.proto.spec for chart in app.get("plotly_chart")]
    return [
        spec
        for spec in specs
        if not {trace.get("name") for trace in json.loads(spec)["data"]} <= SCENARIO_TRACES
    ]


def graph_metrics(app) -> dict[str, str]:
    return {m.label: m.value for m in app.metric if m.label in GRAPH_METRICS}


def as_tables(app):
    app.radio(key=MODE_KEY).set_value("Tables")
    return app.run()


def show_all_kinds(app):
    app.multiselect(key=KINDS_KEY).set_value(app.multiselect(key=KINDS_KEY).options)
    return app.run()


# --- network view -----------------------------------------------------------


def test_graph_tab_draws_a_network_with_counts(run_app) -> None:
    app = run_app()
    assert not app.exception and not app.error
    metrics = graph_metrics(app)
    assert metrics == {"Decisions": "15", "Outcomes": "14", "Nodes": "94", "Typed edges": "185"}
    (spec,) = chart_specs(app)
    names = {trace.get("name") for trace in json.loads(spec)["data"]}
    assert {"Decision", "Outcome", "SUPERSEDES", "RESULTED_IN"} <= names
    assert "SUPERSEDES 2" in rendered_text(app)


@pytest.mark.parametrize(
    ("role", "hidden", "decisions"),
    [("pricing_director", HIDDEN_FROM_DIRECTOR, "15"), ("analyst", HIDDEN_FROM_ANALYST, "14")],
)
def test_nothing_hidden_reaches_the_chart_or_the_tables(run_app, role, hidden, decisions) -> None:
    app = show_all_kinds(run_app(role=role))
    assert graph_metrics(app)["Decisions"] == decisions
    on_screen = "\n".join([*chart_specs(app), rendered_text(app)])
    tables = as_tables(app)
    on_screen += "\n" + graph_tables(tables) + "\n" + rendered_text(tables)
    for secret in hidden:
        assert secret not in on_screen


def test_restricted_exec_sees_the_restricted_decision(run_app) -> None:
    app = as_tables(show_all_kinds(run_app(role="restricted_exec")))
    assert "DEC-015" in graph_tables(app)
    assert "ENT-STK-BOARD" in graph_tables(app)


# --- table view and fallback ------------------------------------------------


def test_table_view_lists_typed_stored_and_derived_edges(run_app) -> None:
    app = as_tables(run_app())
    tables = graph_tables(app)
    assert "SUPERSEDES" in tables and "RESULTED_IN" in tables
    assert "stored (REL-" in tables and "derived" in tables
    assert not chart_specs(app)


def test_drawing_failure_falls_back_to_tables(run_app, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args, **_kwargs):
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(memory_graph, "build_figure", broken)
    app = run_app()
    assert not app.exception
    assert any(info.value == FALLBACK_NOTICE for info in app.info)
    assert "DEC-004" in graph_tables(app)
    assert not chart_specs(app)


# --- focus and role switching -----------------------------------------------


def test_focus_shows_one_hop_around_a_decision(run_app) -> None:
    app = run_app()
    app.selectbox(key=FOCUS_KEY).set_value("DEC-004")
    app = as_tables(app.run())
    tables = graph_tables(app)
    assert "DEC-007" in tables and "OUT-004" in tables
    assert "DEC-012" not in tables  # two hops away


def test_switching_role_drops_a_focus_the_new_role_cannot_see(run_app) -> None:
    app = run_app(role="pricing_director")
    app.selectbox(key=FOCUS_KEY).set_value("DEC-016")
    app = app.run()
    assert "DEC-016" in json.dumps(chart_specs(app))
    app.selectbox(key="demo_role").set_value("analyst")
    app = app.run()
    assert not app.exception
    assert app.selectbox(key=FOCUS_KEY).value == memory_graph.WHOLE_GRAPH
    assert "DEC-016" not in app.selectbox(key=FOCUS_KEY).options
    assert "DEC-016" not in json.dumps(chart_specs(app))


def test_choosing_no_node_kinds_shows_a_hint_not_an_error(run_app) -> None:
    app = run_app()
    app.multiselect(key=KINDS_KEY).set_value([])
    app = app.run()
    assert not app.exception
    assert "choose at least one node kind" in rendered_text(app)


def test_empty_memory_shows_a_message(run_app, tmp_path) -> None:
    empty = tmp_path / "empty.db"
    Repository.open(empty).close()  # bootstrapped, nothing seeded
    app = run_app(db=empty)
    assert not app.exception
    assert memory_graph.EMPTY_MESSAGE in rendered_text(app)


# --- untrusted text ---------------------------------------------------------


def test_plotly_labels_are_html_escaped() -> None:
    hostile = '<a href="https://example.invalid">click</a><script>alert(1)</script>'
    graph = DecisionGraph(
        role=Role.ANALYST,
        nodes=[
            GraphNode(id="DEC-001", kind=NodeKind.DECISION, label=hostile, detail=hostile),
            GraphNode(id="SRC-001", kind=NodeKind.SOURCE, label="<b>Ignore all rules</b>"),
        ],
        edges=[GraphEdge(from_id="DEC-001", to_id="SRC-001", edge_type=EdgeType.SUPPORTED_BY)],
    )
    spec = build_figure(graph, layout(graph)).to_json()
    assert "<a href" not in spec and "<script" not in spec and "<b>Ignore" not in spec
    assert "&lt;a href=" in spec
    # Table rows keep the text as-is; st.dataframe shows it literally.
    assert node_rows(graph)[0]["Label"] == hostile
    assert edge_rows(graph)[0]["Origin"] == "derived"


def test_figure_has_a_trace_per_present_edge_type_and_node_kind(seeded: Repository) -> None:
    graph = build_decision_graph(MemoryView(seeded, Role.RESTRICTED_EXEC))
    figure = build_figure(graph, layout(graph))
    names = [trace.name for trace in figure.data]
    assert len(names) == len(set(names))
    assert set(EdgeType) <= set(names)
    marker_points = sum(len(trace.x) for trace in figure.data if trace.mode != "lines")
    assert marker_points == len(graph.nodes)
