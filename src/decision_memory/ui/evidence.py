"""Local evidence view: the sources the active demo role may see, and one source in detail.

Everything shown here comes through a `MemoryView`, so hidden sources never reach this
module. Source text is rendered with `st.text`, which shows it literally: document
content is data and is never interpreted as Markdown, HTML or instructions.
"""

from __future__ import annotations

import streamlit as st

from decision_memory.authz import MemoryView
from decision_memory.models import SourceDocument
from decision_memory.provenance import MOCK_LINK_NOTE, SOURCE_TYPE_LABELS

EVIDENCE_PARAM = "evidence"
SELECT_KEY = "evidence_select"
LINK_USED_KEY = "evidence_link_used"
UNAVAILABLE_MESSAGE = "That source is not available for this role, or it does not exist."
NO_SOURCES_MESSAGE = (
    "No sources are available. If the demo data has not been loaded yet, "
    "run `python scripts/seed_demo.py` in a terminal and reload."
)


def _option_label(source: SourceDocument) -> str:
    return f"{source.id} · {source.title}"


def render_source_detail(view: MemoryView, source: SourceDocument) -> None:
    """One source in full, plus the visible decisions and outcomes that cite it."""
    type_label = SOURCE_TYPE_LABELS[source.source_type]
    st.text(f"{source.id} · {type_label} (synthetic) · {source.document_date.isoformat()}")
    st.text(source.title)
    st.text(f"Mock reference (not a live link): {source.mock_link}")
    st.caption(MOCK_LINK_NOTE)
    st.text(source.body)

    cited_by = [
        (record.id, label, ref.excerpt)
        for records, label in ((view.decisions(), "decision"), (view.outcomes(), "outcome"))
        for record in records
        for ref in record.source_refs
        if ref.source_id == source.id
    ]
    st.markdown("**Cited by**")
    if not cited_by:
        st.text("Nothing visible to this role cites this source.")
    for record_id, label, excerpt in cited_by:
        st.text(f'{record_id} ({label}): "{excerpt}"')


def render_evidence_explorer(view: MemoryView) -> None:
    """List of visible sources with a detail view, addressable as `?evidence=SRC-001`."""
    st.subheader("Evidence explorer")
    st.caption(
        "Synthetic source documents visible to the selected demo role. "
        "Every citation in this app links back to one of these."
    )
    sources = view.sources()
    if not sources:
        st.info(NO_SOURCES_MESSAGE)
        return

    by_id = {source.id: source for source in sources}
    st.dataframe(
        [
            {
                "Source": source.id,
                "Type": SOURCE_TYPE_LABELS[source.source_type],
                "Title": source.title,
                "Date": source.document_date.isoformat(),
            }
            for source in sources
        ],
        hide_index=True,
    )

    current = st.session_state.get(SELECT_KEY)
    if current is not None and current not in by_id:
        # The role changed and the source that was open is no longer visible.
        del st.session_state[SELECT_KEY]
        st.info(UNAVAILABLE_MESSAGE)
    if not st.session_state.get(LINK_USED_KEY):
        # Honour an evidence link once, when the page is first opened.
        st.session_state[LINK_USED_KEY] = True
        requested = st.query_params.get(EVIDENCE_PARAM)
        if requested in by_id:
            st.session_state[SELECT_KEY] = requested
        elif requested:
            st.info(UNAVAILABLE_MESSAGE)

    selected = st.selectbox(
        "Open a source",
        options=list(by_id),
        index=None,
        format_func=lambda source_id: _option_label(by_id[source_id]),
        placeholder="Choose a source to read it in full",
        key=SELECT_KEY,
    )
    if selected is None:
        if EVIDENCE_PARAM in st.query_params:
            del st.query_params[EVIDENCE_PARAM]
    else:
        st.query_params[EVIDENCE_PARAM] = selected
        render_source_detail(view, by_id[selected])
