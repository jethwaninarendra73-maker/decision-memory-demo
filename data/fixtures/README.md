# Synthetic fixtures

Everything in this folder is fictional: the company, the people, the customers and every number. It exists only to make the demo repeatable.

| File | Contents |
|------|----------|
| `sources.json` + `sources/SRC-*.md` | 22 evidence artifacts (Teams-style transcripts, steering deck excerpts, Jira-style tickets) with the demo roles allowed to see each |
| `entities.json` | Markets, segments, systems, risks, initiatives and stakeholders |
| `decisions.json` | 16 historical freight pricing decisions, each citing exact excerpts from the sources |
| `outcomes.json` | 14 observed outcomes with expected versus actual values |
| `relations.json` | Decision-to-decision links (supersedes, contradicts, similar to, informed by) |
| `capture/demo_transcript.md` | The meeting transcript Agent 1 captures live in the demo. Not seeded: it enters memory only when a reviewer confirms it |

Rules the loader enforces (`src/decision_memory/seed.py`):

- Every source body starts with the `[SYNTHETIC DEMO DATA: ...]` marker line.
- Every `excerpt` must appear exactly once in its source body; offsets are computed from it.
- Entity IDs referenced by a decision must exist in `entities.json`.

Load with `python scripts/seed_demo.py`; restore the baseline with `python scripts/reset_demo.py`.
