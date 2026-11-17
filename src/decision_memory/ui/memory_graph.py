"""Agent 2 view: the permission-filtered decision graph as a network, or as tables.

The graph is rebuilt from SQLite on every rerun. Node labels come from stored records,
some of which quote source documents, so they are treated as untrusted text: in the
Plotly figure they are HTML-escaped (Plotly renders a subset of HTML in hover text and
labels, including links), and in the tables they are shown literally by `st.dataframe`.

The table view is always available, and it is shown automatically whenever the network
cannot be drawn.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Sequence
from typing import Any

import streamlit as st

from decision_memory.agents.memory_agent import (
    KIND_ORDER,
    GraphIntegrityError,
    build_decision_graph,
    edge_type_counts,
    filter_kinds,
    kind_counts,
    layout,
    neighbourhood,
    neighbourhoods,
)
from decision_memory.authz import MemoryView
from decision_memory.models import DERIVED_EDGE_TYPES, DecisionGraph, EdgeType, NodeKind
from decision_memory.ui.view import details, is_simple

logger = logging.getLogger(__name__)

KINDS_KEY = "graph_kinds"
FOCUS_KEY = "graph_focus"
MODE_KEY = "graph_mode"
MODES = ("Network", "Tables")
WHOLE_GRAPH = "__whole__"
PRECEDENTS = "__precedents__"
HIGHLIGHT_NAME = "Search precedent"
HIGHLIGHT_COLOUR = "#facc15"
LESSON_NAME = "Other-region lesson"
LESSON_COLOUR = "#a78bfa"
FALLBACK_NOTICE = "The network view could not be drawn here, so the graph is shown as tables."
EMPTY_MESSAGE = "No confirmed decisions are visible to this role, so the graph is empty."

KIND_LABELS: dict[NodeKind, str] = {
    NodeKind.DECISION: "Decision",
    NodeKind.OUTCOME: "Outcome",
    NodeKind.SOURCE: "Source (synthetic)",
    NodeKind.MARKET: "Market",
    NodeKind.SEGMENT: "Segment",
    NodeKind.CUSTOMER_GROUP: "Customer group",
    NodeKind.SYSTEM: "System",
    NodeKind.RISK: "Risk",
    NodeKind.INITIATIVE: "Initiative",
    NodeKind.STAKEHOLDER: "Stakeholder",
}
KIND_COLOURS: dict[NodeKind, str] = {
    NodeKind.DECISION: "#2563eb",
    NodeKind.OUTCOME: "#16a34a",
    NodeKind.SOURCE: "#6b7280",
    NodeKind.MARKET: "#d97706",
    NodeKind.SEGMENT: "#9333ea",
    NodeKind.CUSTOMER_GROUP: "#db2777",
    NodeKind.SYSTEM: "#0891b2",
    NodeKind.RISK: "#dc2626",
    NodeKind.INITIATIVE: "#65a30d",
    NodeKind.STAKEHOLDER: "#a16207",
}
KIND_SIZES: dict[NodeKind, int] = {NodeKind.DECISION: 18, NodeKind.OUTCOME: 14}
DEFAULT_SIZE = 10
DEFAULT_KINDS: tuple[NodeKind, ...] = (
    NodeKind.DECISION,
    NodeKind.OUTCOME,
    NodeKind.MARKET,
    NodeKind.SEGMENT,
    NodeKind.CUSTOMER_GROUP,
    NodeKind.RISK,
)
# Decision-to-decision edges carry the story, so they are drawn heavier.
EMPHASISED_EDGES: frozenset[EdgeType] = frozenset(
    {EdgeType.SUPERSEDES, EdgeType.CONTRADICTS, EdgeType.SIMILAR_TO, EdgeType.INFORMED_BY}
)
# Explicit edge styles so edges never borrow a node kind's colour from Plotly's cycle.
STRUCTURAL_EDGE_COLOUR = "#9ca3af"
EMPHASISED_EDGE_COLOURS: dict[EdgeType, str] = {
    EdgeType.SUPERSEDES: "#111827",
    EdgeType.CONTRADICTS: "#ea580c",
    EdgeType.SIMILAR_TO: "#0ea5e9",
    EdgeType.INFORMED_BY: "#7c3aed",
}
EMPHASISED_EDGE_DASHES: dict[EdgeType, str] = {
    EdgeType.CONTRADICTS: "dash",
    EdgeType.INFORMED_BY: "dashdot",
}


def safe_text(text: str) -> str:
    """Escape untrusted text for Plotly, which renders a subset of HTML."""
    return html.escape(text, quote=True)


def node_rows(
    graph: DecisionGraph, highlight: Sequence[str] = (), lessons: Sequence[str] = ()
) -> list[dict[str, str]]:
    """Table fallback: one row per node; search precedents carry their rank, lessons from
    other regions are marked as such."""
    ranks = {node_id: f"lesson {rank}" for rank, node_id in enumerate(lessons, start=1)}
    ranks |= {node_id: str(rank) for rank, node_id in enumerate(highlight, start=1)}
    return [
        {
            "Node": node.id,
            "Kind": KIND_LABELS[node.kind],
            "Label": node.label,
            "Detail": node.detail,
            "Precedent rank": ranks.get(node.id, ""),
        }
        for node in graph.nodes
    ]


def edge_rows(graph: DecisionGraph) -> list[dict[str, str]]:
    """Table fallback: one row per typed edge, stored or derived."""
    return [
        {
            "From": edge.from_id,
            "Edge type": edge.edge_type.value,
            "To": edge.to_id,
            "Origin": "derived" if edge.derived else f"stored ({edge.relation_id})",
            "Note": edge.note,
        }
        for edge in graph.edges
    ]


def build_figure(
    graph: DecisionGraph,
    positions: dict[str, tuple[float, float]],
    highlight: Sequence[str] = (),
    lessons: Sequence[str] = (),
) -> Any:
    """Plotly figure of the graph: one trace per edge type and one per node kind, plus a
    ring around each search precedent (`highlight`) and each lesson from another region
    (`lessons`) that is in the graph, in two different colours."""
    import plotly.graph_objects as go

    figure = go.Figure()
    for ids, name, colour in (
        (highlight, HIGHLIGHT_NAME, HIGHLIGHT_COLOUR),
        (lessons, LESSON_NAME, LESSON_COLOUR),
    ):
        rings = [node_id for node_id in ids if node_id in positions]
        if not rings:
            continue
        figure.add_trace(
            go.Scatter(
                x=[positions[node_id][0] for node_id in rings],
                y=[positions[node_id][1] for node_id in rings],
                mode="markers",
                name=name,
                legendgroup="search",
                legendgrouptitle_text="Search",
                hoverinfo="skip",
                marker={"size": 34, "color": colour, "opacity": 0.55},
            )
        )
    for edge_type in EdgeType:
        edges = [edge for edge in graph.edges if edge.edge_type is edge_type]
        if not edges:
            continue
        xs: list[float | None] = []
        ys: list[float | None] = []
        for edge in edges:
            (x0, y0), (x1, y1) = positions[edge.from_id], positions[edge.to_id]
            xs += [x0, x1, None]
            ys += [y0, y1, None]
        emphasised = edge_type in EMPHASISED_EDGES
        figure.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                name=edge_type.value,
                legendgroup="edges",
                legendgrouptitle_text="Edges",
                hoverinfo="skip",
                line={
                    "width": 2.5 if emphasised else 0.8,
                    "color": EMPHASISED_EDGE_COLOURS.get(edge_type, STRUCTURAL_EDGE_COLOUR),
                    "dash": (
                        "dot"
                        if edge_type in DERIVED_EDGE_TYPES
                        else EMPHASISED_EDGE_DASHES.get(edge_type, "solid")
                    ),
                },
                opacity=0.9 if emphasised else 0.45,
            )
        )
    for kind in KIND_ORDER:
        nodes = [node for node in graph.nodes if node.kind is kind]
        if not nodes:
            continue
        figure.add_trace(
            go.Scatter(
                x=[positions[node.id][0] for node in nodes],
                y=[positions[node.id][1] for node in nodes],
                mode="markers+text" if kind is NodeKind.DECISION else "markers",
                name=KIND_LABELS[kind],
                legendgroup="nodes",
                legendgrouptitle_text="Nodes",
                text=[node.id for node in nodes] if kind is NodeKind.DECISION else None,
                textposition="top center",
                hovertext=[
                    "<br>".join(
                        part
                        for part in (
                            f"<b>{safe_text(node.id)}</b> · {KIND_LABELS[kind]}",
                            safe_text(node.label),
                            safe_text(node.detail),
                        )
                        if part
                    )
                    for node in nodes
                ],
                hoverinfo="text",
                marker={
                    "size": KIND_SIZES.get(kind, DEFAULT_SIZE),
                    "color": KIND_COLOURS[kind],
                    "line": {"width": 1, "color": "#ffffff"},
                },
            )
        )
    figure.update_layout(
        height=620,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        xaxis={"visible": False},
        yaxis={"visible": False},
        hovermode="closest",
        legend={"groupclick": "toggleitem"},
    )
    return figure


def render_tables(
    graph: DecisionGraph, highlight: Sequence[str] = (), lessons: Sequence[str] = ()
) -> None:
    st.markdown(f"**Nodes** ({len(graph.nodes)})")
    st.dataframe(node_rows(graph, highlight, lessons), hide_index=True, key="graph_nodes_table")
    st.markdown(f"**Edges** ({len(graph.edges)})")
    st.dataframe(edge_rows(graph), hide_index=True, key="graph_edges_table")


def render_network(
    graph: DecisionGraph, highlight: Sequence[str] = (), lessons: Sequence[str] = ()
) -> None:
    """Draw the network; on any drawing failure, fall back to the tables."""
    try:
        figure = build_figure(graph, layout(graph), highlight, lessons)
        st.plotly_chart(figure, key="graph_network")
    except Exception:  # any rendering failure must degrade to tables, never crash the tab
        logger.exception("Network view failed; showing the table fallback")
        st.info(FALLBACK_NOTICE)
        render_tables(graph, highlight, lessons)
        return
    st.caption(
        "Hover a node for its detail. Thick lines link decisions to each other; "
        "dotted lines are derived from citations and outcomes. Click a legend entry to hide it."
    )


def _choose_view(graph: DecisionGraph, highlight: Sequence[str]) -> tuple[DecisionGraph, str]:
    """Controls for node kinds, focus decision and display mode."""
    available = list(kind_counts(graph))
    if KINDS_KEY not in st.session_state:
        st.session_state[KINDS_KEY] = [k.value for k in DEFAULT_KINDS if k in available]
    else:  # after a role switch, drop kinds this role no longer has
        st.session_state[KINDS_KEY] = [
            value for value in st.session_state[KINDS_KEY] if NodeKind(value) in available
        ]
    decisions = {node.id: node.label for node in graph.nodes if node.kind is NodeKind.DECISION}
    focus_options = [WHOLE_GRAPH, *([PRECEDENTS] if highlight else []), *decisions]
    if st.session_state.get(FOCUS_KEY) not in (None, *focus_options):
        # focus was on a now-hidden decision, or there are no search results any more
        st.session_state[FOCUS_KEY] = WHOLE_GRAPH

    kinds_col, focus_col, mode_col = st.columns([3, 3, 1])
    with kinds_col:
        kinds = st.multiselect(
            "Show node kinds",
            options=[kind.value for kind in available],
            format_func=lambda value: KIND_LABELS[NodeKind(value)],
            key=KINDS_KEY,
        )
    with focus_col:
        focus = st.selectbox(
            "Focus",
            options=focus_options,
            format_func=lambda value: (
                "Whole graph"
                if value == WHOLE_GRAPH
                else "Search results: precedents and lessons (1 hop)"
                if value == PRECEDENTS
                else f"{value} · {decisions[value]} (1 hop)"
            ),
            key=FOCUS_KEY,
        )
    with mode_col:
        mode = st.radio("View", MODES, key=MODE_KEY)

    if focus in (None, WHOLE_GRAPH):
        shown = graph
    elif focus == PRECEDENTS:
        shown = neighbourhoods(graph, highlight)
    else:
        shown = neighbourhood(graph, focus)
    return filter_kinds(shown, (NodeKind(value) for value in kinds)), mode


def render_memory_graph(
    view: MemoryView, highlight: Sequence[str] = (), lessons: Sequence[str] = ()
) -> None:
    """The decision graph, with search precedents and other-region lessons highlighted."""
    st.markdown("#### Decision graph")
    if is_simple():
        st.caption(
            "How past decisions, their outcomes, markets, risks and sources connect. The "
            "decisions found above are highlighted."
        )
    try:
        graph = build_decision_graph(view)
    except GraphIntegrityError as exc:
        st.error(str(exc))
        return
    if not graph.node_ids(NodeKind.DECISION):
        st.info(EMPTY_MESSAGE)
        return

    counts = kind_counts(graph)
    metrics = st.columns(4)
    metrics[0].metric("Decisions", counts.get(NodeKind.DECISION, 0))
    metrics[1].metric("Outcomes", counts.get(NodeKind.OUTCOME, 0))
    metrics[2].metric("Nodes", len(graph.nodes))
    metrics[3].metric("Typed edges", len(graph.edges))
    highlight = [node_id for node_id in highlight if graph.node(node_id) is not None]
    lessons = [node_id for node_id in lessons if graph.node(node_id) is not None]
    with details("graph options and counts"):
        st.caption(
            "A projection of the SQLite memory, rebuilt on every view and filtered for the "
            "selected demo role before anything is drawn. Only human-confirmed decisions appear."
        )
        st.caption(
            "Edge types: "
            + " · ".join(
                f"{edge_type.value} {count}" for edge_type, count in edge_type_counts(graph).items()
            )
        )
        shown, mode = _choose_view(graph, [*highlight, *lessons])
        if shown.nodes:
            st.caption(f"Showing {len(shown.nodes)} nodes and {len(shown.edges)} edges.")
        if highlight:
            st.caption("Highlighted search precedents: " + ", ".join(highlight))
        if lessons:
            st.caption("Highlighted lessons from other regions: " + ", ".join(lessons))
    if not shown.nodes:
        st.info("Nothing to show: choose at least one node kind.")
        return
    if mode == MODES[0]:
        render_network(shown, highlight, lessons)
    else:
        render_tables(shown, highlight, lessons)
