"""Agent 1 — Decision Capture.

A deterministic parser for a structured, synthetic Teams-style meeting transcript. It
proposes a `DecisionDraft`; a human reviews, edits and confirms it before anything
enters institutional memory.

How a transcript is read:
- Header lines `Meeting:`, `Date:` (YYYY-MM-DD) and `Attendees:` describe the meeting.
- A decision is a line that starts with `DECISION:`. Nothing else counts: not a speaker
  saying "we should" or "we decided", not `NO DECISION:`, `PARKED:`, `ACTION:` or
  `PROPOSAL:` lines. Those are listed as "not treated as decisions" so a reviewer can see
  what was left out and why.
- `OWNER:`, `REGION:`, `MARKET:`, `SEGMENT:`, `RATIONALE:`, `ALTERNATIVE:`, `RISK:` and
  `EXPECTED OUTCOME:` lines after a decision describe that decision.

The parser never guesses. A field that is absent, a placeholder ("TBC") or contradictory
is left empty and flagged. Every extracted value carries the exact character offsets of
the snippet it came from.

The transcript is untrusted DATA. It is only ever sliced and matched as text. Nothing in
it is executed, evaluated, imported, rendered or followed as an instruction.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import PurePath

from ..audit import PermissionDeniedError, capture_decision, review_decision
from ..authz import MemoryView
from ..models import (
    CaptureResult,
    Decision,
    DecisionDraft,
    DecisionStatus,
    DraftField,
    EntityKind,
    EvidenceSpan,
    FieldFlag,
    FlagKind,
    NonDecision,
    Relation,
    RelationType,
    ReviewedDecision,
    Role,
    SourceDocument,
    SourceRef,
    SourceType,
)
from ..repository import Repository
from .base import AgentInfo

INFO = AgentInfo(
    number=1,
    key="capture",
    name="Decision Capture",
    tab_name="Capture a decision",
    purpose="Extracts a decision draft from a synthetic meeting transcript for human review.",
    reads="Synthetic Teams-style transcript (.txt / .md, pasted or fixture)",
    produces="Decision draft with stakeholders, rationale, risks and exact evidence offsets",
    build_stage=4,
)

SYNTHETIC_MARKER = "[SYNTHETIC DEMO DATA:"
CAPTURED_AUTHOR = "Captured in the demo app"  # author label of every transcript captured here
MAX_TRANSCRIPT_CHARS = 100_000
ALLOWED_SUFFIXES = (".txt", ".md")

# Rule-based field confidence: how explicitly the transcript states the value.
EXPLICIT = 0.9  # read from its own labelled line
INFERRED = 0.6  # derived, or stated but inconsistent with the rest of the transcript
HEDGED = 0.3  # stated, but the wording says it may not be final

_LABELLED_LINE = re.compile(
    r"^[ \t]*(?:[-*•][ \t]+)?(?P<label>[A-Za-z][A-Za-z .'\-]{0,40}?)[ \t]*:[ \t]*(?P<value>.*)$"
)
_HEADER_LABELS = {"MEETING": "MEETING", "DATE": "DATE", "ATTENDEES": "ATTENDEES", "PARTICIPANTS": "ATTENDEES"}
_ATTRIBUTE_LABELS = {
    "OWNER": "OWNER",
    "REGION": "REGION",
    "MARKET": "MARKET",
    "SEGMENT": "SEGMENT",
    "CUSTOMER SEGMENT": "SEGMENT",
    "RATIONALE": "RATIONALE",
    "REASON": "RATIONALE",
    "ALTERNATIVE": "ALTERNATIVE",
    "ALTERNATIVES": "ALTERNATIVE",
    "RISK": "RISK",
    "RISKS": "RISK",
    "EXPECTED OUTCOME": "EXPECTED OUTCOME",
}
_PLURAL_LABELS = {"ALTERNATIVES", "RISKS"}
_NON_DECISION_LABELS = {
    "NO DECISION": "Marked NO DECISION: the topic was left open.",
    "DECISION PENDING": "Marked DECISION PENDING: not decided yet.",
    "OPEN QUESTION": "Marked OPEN QUESTION: not decided yet.",
    "PARKED": "Marked PARKED: set aside for later.",
    "DEFERRED": "Marked DEFERRED: set aside for later.",
    "PROPOSAL": "Marked PROPOSAL: suggested, not decided.",
    "ACTION": "Marked ACTION: a task, not a decision.",
}
_SPOKEN_CUE = re.compile(
    r"\b(decid\w*|decision\w*|agree\w*|approv\w*|we should|let'?s|let us|go with|go to)\b",
    re.IGNORECASE,
)
_SPOKEN_REASON = "Said in discussion; there is no DECISION line for it."
_VOID_DECISION = re.compile(
    r"^(none|no decision|nothing decided|n/?a|tbc|tbd|deferred|postponed|pending)\b", re.IGNORECASE
)
_HEDGE = re.compile(
    r"\b(tentative(?:ly)?|provisional(?:ly)?|probably|possibly|maybe|perhaps|might|"
    r"subject to|pending|to be confirmed|tbc|tbd|if approved|in principle)\b",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(
    r"^(tbc|tbd|to be confirmed|to be decided|unknown|n/?a|none|unassigned|\?+|-+)\.?$",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SURCHARGE_RATE = re.compile(r"(\d+(?:\.\d+)?)%\s+(?:[A-Za-z-]+\s+){0,2}surcharge", re.IGNORECASE)
_TITLE_LENGTH = 120


class TranscriptError(ValueError):
    """The pasted or uploaded transcript cannot be used. The message is safe to show."""


class CaptureError(Exception):
    """A draft could not be saved."""


class DuplicateCaptureError(CaptureError):
    """This exact transcript has already been captured."""


# --- reading input ------------------------------------------------------------


def normalise_transcript(text: str) -> str:
    """Validate pasted text and normalise line endings. Offsets refer to the result."""
    text = text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise TranscriptError("This does not look like a text file.")
    if not text.strip():
        raise TranscriptError("The transcript is empty.")
    if len(text) > MAX_TRANSCRIPT_CHARS:
        raise TranscriptError(
            f"The transcript is longer than {MAX_TRANSCRIPT_CHARS:,} characters."
        )
    return text


def read_transcript_upload(filename: str, data: bytes) -> str:
    """Decode an uploaded `.txt` or `.md` file as UTF-8 text. The file is never run."""
    if PurePath(filename).suffix.lower() not in ALLOWED_SUFFIXES:
        raise TranscriptError("Only .txt and .md files are accepted.")
    if len(data) > MAX_TRANSCRIPT_CHARS * 4:
        raise TranscriptError("The file is too large.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise TranscriptError("The file is not UTF-8 text.") from None
    return normalise_transcript(text)


# --- parsing ------------------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    """One transcript line. `label` is None for plain text; offsets are into the transcript."""

    text: str
    start: int
    label: str | None = None
    value: str = ""
    value_start: int = 0

    @property
    def value_end(self) -> int:
        return self.value_start + len(self.value)

    @property
    def end(self) -> int:
        return self.start + len(self.text)


@dataclass
class _Block:
    decision: _Line
    attributes: dict[str, list[_Line]] = field(default_factory=dict)
    plural: set[int] = field(default_factory=set)  # ids of lines written with a plural label


def _lines(transcript: str) -> Iterator[_Line]:
    position = 0
    for raw in transcript.split("\n"):
        match = _LABELLED_LINE.match(raw)
        stripped = raw.rstrip()
        if match and stripped:
            value = match.group("value").rstrip()
            yield _Line(
                text=stripped,
                start=position,
                label=" ".join(match.group("label").upper().split()),
                value=value,
                value_start=position + match.start("value"),
            )
        elif stripped:
            yield _Line(text=stripped, start=position)
        position += len(raw) + 1


def _span(draft_field: DraftField, line: _Line) -> EvidenceSpan:
    return EvidenceSpan(
        field=draft_field,
        excerpt=line.value,
        start_offset=line.value_start,
        end_offset=line.value_end,
    )


def _not_decision(line: _Line, reason: str) -> NonDecision:
    stripped = line.text.lstrip()
    start = line.start + len(line.text) - len(stripped)
    return NonDecision(excerpt=stripped, start_offset=start, end_offset=line.end, reason=reason)


class _DraftBuilder:
    """Collects values, confidences, flags and evidence for one decision."""

    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.confidence: dict[DraftField, float] = {}
        self.flags: list[FieldFlag] = []
        self.evidence: list[EvidenceSpan] = []

    def set(self, draft_field: DraftField, value: object, confidence: float, lines: list[_Line]) -> None:
        self.values[draft_field.value] = value
        self.confidence[draft_field] = confidence
        self.evidence += [_span(draft_field, line) for line in lines]

    def flag(self, draft_field: DraftField, kind: FlagKind, reason: str) -> None:
        self.flags.append(FieldFlag(field=draft_field, kind=kind, reason=reason))

    def missing(self, draft_field: DraftField, reason: str) -> None:
        self.flag(draft_field, FlagKind.MISSING, reason)

    def single(self, draft_field: DraftField, label: str, lines: list[_Line]) -> str | None:
        """Read a field that must have exactly one value; otherwise flag it and leave it empty."""
        lines = [line for line in lines if line.value]
        distinct = list(dict.fromkeys(line.value for line in lines))
        if not lines:
            self.missing(draft_field, f"No {label} line is recorded for this decision.")
        elif len({value.casefold() for value in distinct}) > 1:
            self.missing(
                draft_field, f"Conflicting {label} lines ({'; '.join(distinct)}); not guessed."
            )
        elif _PLACEHOLDER.match(distinct[0]):
            self.missing(draft_field, f"{label} is given as “{distinct[0]}”; not guessed.")
        else:
            self.set(draft_field, distinct[0], EXPLICIT, lines[:1])
            return distinct[0]
        return None

    def many(self, draft_field: DraftField, label: str, block: _Block, *, required: bool) -> None:
        lines = [line for line in block.attributes.get(label, []) if line.value]
        values: list[str] = []
        for line in lines:
            parts = line.value.split(";") if id(line) in block.plural else [line.value]
            values += [part.strip() for part in parts if part.strip()]
        if values:
            self.set(draft_field, values, EXPLICIT, lines)
        elif required:
            self.missing(draft_field, f"No {label} line is recorded for this decision.")


def _title(statement: str) -> str:
    text = statement.rstrip(". ")
    if len(text) <= _TITLE_LENGTH:
        return text
    return text[:_TITLE_LENGTH].rsplit(" ", 1)[0] + "…"


def _build_draft(
    block: _Block, header: dict[str, _Line], known_markets: Mapping[str, str]
) -> DecisionDraft:
    builder = _DraftBuilder()
    statement = block.decision.value

    hedge = _HEDGE.search(statement)
    builder.set(DraftField.STATEMENT, statement, HEDGED if hedge else EXPLICIT, [block.decision])
    if hedge:
        builder.flag(
            DraftField.STATEMENT,
            FlagKind.UNCERTAIN,
            f"The wording (“{hedge.group(0)}”) suggests this may not be final.",
        )

    date_line = header.get("DATE")
    if date_line is None or not date_line.value:
        builder.missing(DraftField.DECISION_DATE, "No Date line in the transcript header.")
    else:
        parsed = _parse_date(date_line.value)
        if parsed is None:
            builder.missing(
                DraftField.DECISION_DATE,
                f"Date “{date_line.value}” is not a valid YYYY-MM-DD date; not guessed.",
            )
        else:
            builder.set(DraftField.DECISION_DATE, parsed, EXPLICIT, [date_line])

    attendees: list[str] = []
    attendee_line = header.get("ATTENDEES")
    if attendee_line is not None:
        attendees = [name.strip() for name in re.split(r"[,;]", attendee_line.value) if name.strip()]
    if attendees:
        builder.set(DraftField.STAKEHOLDERS, attendees, EXPLICIT, [attendee_line])
    else:
        builder.missing(DraftField.STAKEHOLDERS, "No Attendees line in the transcript header.")

    owner = builder.single(DraftField.OWNER, "OWNER", block.attributes.get("OWNER", []))
    if owner and attendees and owner.casefold() not in {name.casefold() for name in attendees}:
        builder.confidence[DraftField.OWNER] = INFERRED
        builder.flag(DraftField.OWNER, FlagKind.UNCERTAIN, "The owner is not in the attendee list.")

    market = builder.single(DraftField.MARKET, "MARKET", block.attributes.get("MARKET", []))
    region_lines = block.attributes.get("REGION", [])
    known_region = {name.casefold(): region for name, region in known_markets.items()}.get(
        (market or "").casefold()
    )
    if not region_lines and known_region:
        builder.set(DraftField.REGION, known_region, INFERRED, [])
        builder.flag(
            DraftField.REGION,
            FlagKind.UNCERTAIN,
            "Taken from the market list in memory, not from the transcript.",
        )
    else:
        builder.single(DraftField.REGION, "REGION", region_lines)
    builder.single(DraftField.CUSTOMER_SEGMENT, "SEGMENT", block.attributes.get("SEGMENT", []))

    rationale_lines = [line for line in block.attributes.get("RATIONALE", []) if line.value]
    if rationale_lines:
        text = " ".join(line.value for line in rationale_lines)
        builder.set(DraftField.RATIONALE, text, EXPLICIT, rationale_lines)
    else:
        builder.missing(DraftField.RATIONALE, "No RATIONALE line is recorded for this decision.")

    builder.many(DraftField.RISKS, "RISK", block, required=True)
    builder.many(DraftField.ALTERNATIVES, "ALTERNATIVE", block, required=False)
    builder.single(
        DraftField.EXPECTED_OUTCOME, "EXPECTED OUTCOME", block.attributes.get("EXPECTED OUTCOME", [])
    )

    rates = {match.group(1) for match in _SURCHARGE_RATE.finditer(statement)}
    if len(rates) == 1:
        match = _SURCHARGE_RATE.search(statement)
        rate = float(match.group(1)) / 100
        if 0 <= rate <= 1:
            builder.values[DraftField.SURCHARGE_RATE.value] = rate
            builder.confidence[DraftField.SURCHARGE_RATE] = INFERRED
            start = block.decision.value_start + match.start(1)
            builder.evidence.append(
                EvidenceSpan(
                    field=DraftField.SURCHARGE_RATE,
                    excerpt=f"{match.group(1)}%",
                    start_offset=start,
                    end_offset=start + len(match.group(1)) + 1,
                )
            )

    return DecisionDraft(
        title=_title(statement),
        field_confidence=builder.confidence,
        flags=builder.flags,
        evidence=builder.evidence,
        **builder.values,
    )


def _parse_date(value: str) -> date | None:
    if not _ISO_DATE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def extract_decisions(
    transcript: str, *, known_markets: Mapping[str, str] | None = None
) -> CaptureResult:
    """Parse a transcript into decision drafts and a list of what was not a decision.

    `known_markets` maps market names already in memory to their region. It is used only
    to fill in a region when the transcript names a known market and gives no region.
    """
    transcript = normalise_transcript(transcript)
    header: dict[str, _Line] = {}
    blocks: list[_Block] = []
    not_decisions: list[NonDecision] = []
    warnings: list[str] = []
    current: _Block | None = None
    orphan_attributes = False

    for line in _lines(transcript):
        label = line.label
        if label == "DECISION":
            if not line.value or _VOID_DECISION.match(line.value):
                not_decisions.append(
                    _not_decision(line, "The DECISION line says no decision was made.")
                )
                current = None
            else:
                current = _Block(decision=line)
                blocks.append(current)
        elif label in _ATTRIBUTE_LABELS:
            if current is None:
                orphan_attributes = True
            else:
                current.attributes.setdefault(_ATTRIBUTE_LABELS[label], []).append(line)
                if label in _PLURAL_LABELS:
                    current.plural.add(id(line))
        elif label in _NON_DECISION_LABELS:
            not_decisions.append(_not_decision(line, _NON_DECISION_LABELS[label]))
        elif label in _HEADER_LABELS and not blocks:
            header.setdefault(_HEADER_LABELS[label], line)
        elif _SPOKEN_CUE.search(line.value if label else line.text):
            not_decisions.append(_not_decision(line, _SPOKEN_REASON))

    has_marker = transcript.lstrip().startswith(SYNTHETIC_MARKER)
    if not has_marker:
        warnings.append(
            "This text does not start with the synthetic-data marker. "
            "Use fictional text only; never paste real company or customer data."
        )
    if not blocks:
        warnings.append("No DECISION line was found, so nothing was extracted.")
    if orphan_attributes:
        warnings.append("Some detail lines appear before any DECISION line and were ignored.")

    meeting = header.get("MEETING")
    return CaptureResult(
        meeting_title=meeting.value if meeting and meeting.value else None,
        has_synthetic_marker=has_marker,
        drafts=[_build_draft(block, header, known_markets or {}) for block in blocks],
        not_decisions=not_decisions,
        warnings=warnings,
    )


# --- human confirmation ---------------------------------------------------------


@dataclass(frozen=True)
class CaptureOutcome:
    """What was stored when a reviewer confirmed a draft."""

    decision: Decision
    source: SourceDocument
    edited_fields: list[str]
    linked_entity_ids: list[str]


def edited_fields(draft: DecisionDraft, reviewed: ReviewedDecision) -> list[str]:
    """Names of fields the reviewer changed or filled in, compared with the extraction."""
    changed = []
    for name in ReviewedDecision.model_fields:
        before, after = getattr(draft, name), getattr(reviewed, name)
        if (before or None) != (after or None):
            changed.append(name)
    return changed


def _link_to_entities(
    repo: Repository, view: MemoryView, decision: Decision, now: datetime
) -> list[str]:
    """Link the decision to entities already in memory whose names match exactly.

    No entity is created and nothing is matched loosely. Only entities the confirming
    role may see are considered.
    """
    by_kind: dict[EntityKind, dict[str, str]] = {}
    for entity in view.entities():
        by_kind.setdefault(entity.kind, {})[entity.name.casefold()] = entity.id

    wanted: list[tuple[EntityKind, str, RelationType]] = [
        (EntityKind.STAKEHOLDER, decision.owner, RelationType.OWNED_BY),
        *[
            (EntityKind.STAKEHOLDER, name, RelationType.INVOLVES)
            for name in decision.stakeholders
            if name.casefold() != decision.owner.casefold()
        ],
        (EntityKind.MARKET, decision.market, RelationType.APPLIES_TO),
        (EntityKind.SEGMENT, decision.customer_segment, RelationType.APPLIES_TO),
        *[(EntityKind.RISK, risk, RelationType.HAS_RISK) for risk in decision.risks],
    ]
    linked: list[str] = []
    for kind, name, relation_type in wanted:
        entity_id = by_kind.get(kind, {}).get(name.casefold())
        if entity_id is None or entity_id in linked:
            continue
        repo.add(
            Relation(
                id=repo.next_id(Relation),
                from_id=decision.id,
                to_id=entity_id,
                relation_type=relation_type,
                created_at=now,
                updated_at=now,
            )
        )
        linked.append(entity_id)
    return linked


def confirm_capture(
    repo: Repository,
    *,
    transcript: str,
    draft: DecisionDraft,
    reviewed: ReviewedDecision,
    meeting_title: str | None,
    access_roles: list[Role],
    actor_label: str,
    role: Role,
    now: datetime,
) -> CaptureOutcome:
    """Store a human-confirmed decision, its transcript as the source, and the audit trail.

    Everything is written in one transaction: the transcript as a source document, the
    decision (captured as a draft, then confirmed), links to matching entities, and two
    audit events naming who captured and who confirmed. If any step fails, nothing is saved.
    """
    transcript = normalise_transcript(transcript)
    if role not in access_roles:
        raise PermissionDeniedError(
            "The role confirming a decision must be allowed to see its source."
        )
    for span in draft.evidence:
        if transcript[span.start_offset : span.end_offset] != span.excerpt:
            raise CaptureError("The draft does not match this transcript. Extract it again.")

    with repo.transaction():
        # Only copies this role can see count. Refusing because of a copy the role cannot
        # see would reveal that a hidden record exists; it must look like a missing one.
        if repo.find_visible(SourceDocument, role, body=transcript):
            raise DuplicateCaptureError("This transcript has already been captured.")
        source_id = repo.next_id(SourceDocument)
        source = repo.add(
            SourceDocument(
                id=source_id,
                source_type=SourceType.TEAMS_TRANSCRIPT,
                title=meeting_title or "Captured meeting transcript",
                document_date=reviewed.decision_date,
                author_label=CAPTURED_AUTHOR,
                body=transcript,
                access_roles=access_roles,
                # A label, not a real link: nothing in this app connects to Teams.
                mock_link=f"mock://teams_transcript/{source_id}",
                region=reviewed.region,
                synthetic=transcript.lstrip().startswith(SYNTHETIC_MARKER),
                created_at=now,
                updated_at=now,
            )
        )
        spans = {(span.start_offset, span.end_offset): span for span in draft.evidence}
        refs = [
            SourceRef(
                source_id=source.id,
                excerpt=span.excerpt,
                start_offset=span.start_offset,
                end_offset=span.end_offset,
            )
            for span in spans.values()
        ]
        captured = capture_decision(
            repo,
            Decision(
                id=repo.next_id(Decision),
                status=DecisionStatus.DRAFT,
                source_refs=refs,
                synthetic=source.synthetic,
                created_at=now,
                updated_at=now,
                **reviewed.model_dump(),
            ),
            actor_label=actor_label,
            role=role,
            now=now,
        )
        changed = edited_fields(draft, reviewed)
        confirmed = review_decision(
            repo,
            captured.model_copy(update={"status": DecisionStatus.CONFIRMED}),
            actor_label=actor_label,
            role=role,
            now=now,
            note=f"Reviewer edited: {', '.join(changed)}." if changed else "No reviewer edits.",
        )
        linked = _link_to_entities(repo, MemoryView(repo, role), confirmed, now)
    return CaptureOutcome(
        decision=confirmed, source=source, edited_fields=changed, linked_entity_ids=linked
    )
