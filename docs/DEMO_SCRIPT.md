# Demo script: five minutes, six steps

**SYNTHETIC DATA — NOT A PREDICTION OR AUTHORIZED BUSINESS DECISION.** The company, people, customers and numbers are fictional. Say so at the start.

The question on screen: *"Should we increase the freight surcharge by 5% for European customers next quarter?"*

This script follows the **Guided demo** panel at the top of the app. The panel shows the same steps, the same lines to say and live checks that turn green as you go. Use **Next step ▶** to move on; it opens the right tab for you.

Prepare with [DEMO_CHECKLIST.md](DEMO_CHECKLIST.md). The one thing that must be true before you start: the demo data is at the baseline (sidebar **Demo reset**) and the role is **Pricing Director**.

## At a glance

| Step | Time | Where | The point to land |
|------|------|-------|-------------------|
| 1. Hook | 0:00–0:30 | the guide panel | The answer exists, scattered across three places |
| 2. Capture | 0:30–1:15 | tab 1 | Agent 1 proposes, a person confirms |
| 3. Ask | 1:15–2:15 | tab 2 | Cited precedents; a failed one is a warning |
| 4. Analyze | 2:15–3:30 | tabs 3 and 4 | Sourced or labelled as assumed; visible formulas |
| 5. Recommend | 3:30–4:30 | tab 5 | A guarded, cited, advisory recommendation |
| 6. Close the loop | 4:30–5:00 | tabs 6 and 2 | A human approves; the outcome becomes memory |

## 1. Hook (0:00–0:30)

**Do**
- Start on the guide panel, step 1. Three cards show a Teams-style transcript (SRC-006), a steering deck excerpt (SRC-014) and a Jira-style ticket (SRC-002).
- Point at each card's quoted line. Do not open them; **Read in full below** is there if someone asks.

**Say**
> The company has made this call before. The answer is spread across transcripts, decks and tickets that nobody can find in time.

Then: "Everything you will see is synthetic, runs on this laptop with no network, and only recommends. A person decides."

## 2. Capture (0:30–1:15)

**Do**
- **Next step ▶** opens tab **1 · Decision Capture**. **Demo transcript** is already selected.
- Click **Extract decision**.
- Point at one field's evidence (the exact characters it was read from) and at **Not treated as decisions**: the "go to 7% everywhere" remark and the open Q1 question were left out.
- **Confirmed by** is prefilled. Click **Confirm and save to memory**.

**Say**
> Agent 1 proposes, a person confirms. Nothing enters memory unreviewed.

**On screen:** a green banner confirms `DEC-017`, and its transcript opens below as `SRC-023`. Live check: "Captured in the app and confirmed: DEC-017."

## 3. Ask (1:15–2:15)

**Do**
- **Next step ▶** opens tab **2 · Institutional Memory**. The question is already filled in.
- Walk down the precedents: UK and Ireland and Italy at 5% (partial), France at 5% for non-contract customers (success), Germany at 3% (success).
- Stop on the warning: Germany, 6% for all customers, 2023, **failed** (`DEC-001`).
- Point at the lesson from another region: Singapore (`DEC-014`), shown for contrast only.
- Scroll to the graph: the precedents are highlighted.

**Say**
> Every claim cites an exact source excerpt. A failed decision is a warning, never a template.

**On screen:** live checks "Precedents returned: DEC-009, DEC-007, DEC-004, DEC-011, DEC-001", "Shown as warnings (failed): DEC-001" and "Lessons from other regions: DEC-014".

## 4. Analyze (2:15–3:30)

**Do**
- **Next step ▶** opens tab **3 · Impact Analysis**. Point at one **source-supported** item (it quotes a precedent) and one **assumed for demo** item.
- Tick **Exclude high-volume contract customers**. The live check turns green: "High-volume contract customers are excluded in the draft proposal."
- Click **Open 4 · Scenario Simulation** on the guide. Three columns: +3%, +5%, +7%.
- Read the +5% formula aloud from the guide's talk track (below). If there is time, change one input and let the figures recompute, then click **Restore default inputs**.

**Say**
> Each impact item is either quoted from a precedent or labelled as an assumption. The figures come from visible formulas, not from a model.

> Where does +$14.00M come from? $400.00M × 5% × 90% not exempt = +$18.00M, minus $400.00M × 1% churn loss = $4.00M, gives +$14.00M revenue change a quarter, before margin.

**On screen (default inputs):**

| Option | Revenue change a quarter | Margin change a quarter | Churn loss against the 1.2% guardrail |
|--------|--------------------------|-------------------------|----------------------------------------|
| +3% | +$8.40M | +$5.58M | 0.6%, within |
| +5% | +$14.00M | +$9.50M | 1.0%, within |
| +7% | +$18.80M | +$12.86M | 1.6%, exceeds |

These are illustrations from editable synthetic assumptions, not forecasts. The talk track on the guide is rebuilt from the figures on screen, so read it from there if you edited an input.

## 5. Recommend (3:30–4:30)

**Do**
- **Next step ▶** opens tab **5 · Recommendation**.
- Read the card's headline: **Advisory recommendation: Phased pilot at +5%**.
- Point at **How each option was judged**: +7% is excluded because its 1.6% churn loss exceeds the 1.2% guardrail; +3% is eligible but has the lower uplift.
- Point at the two key risks (churn among high-volume contract customers, competitive response in Germany) and at one citation under a reason.
- Point at the line that names the approval it needs.

**Say**
> The recommendation follows written rules, cites its reasons and abstains without evidence.

**On screen:** live check "Recommendation on screen: Phased pilot at +5%; 2 key risk(s) cited."

## 6. Close the loop (4:30–5:00)

**Do**
- **Next step ▶** opens tab **6 · Governance**.
- Your name and the pilot markets are already filled in (the checklist does this before you start). If not, type a fictional name in **Your name** and choose two pilot markets (France and Italy). Click **Approve (simulated)**.
- Click **Record week 1 KPI** (prefilled with the plan), then **Close the pilot and write the outcome to memory**.
- Click **Open 2 · Institutional Memory** on the guide: the pilot decision is now the first precedent, with its outcome; Germany 2023 is still there as a warning.

**Say**
> A human approves, the pilot is tracked, and its outcome becomes institutional memory for the next decision.

**On screen:** "Pilot PRO-001 is completed." and "Outcome OUT-015 is in memory (tab 2)." The pilot decision is `DEC-018`.

**Close with:** "Next quarter, whoever asks this question starts from here, not from scratch."

## If you are short on time

Cut in this order; the story still holds:

1. Step 4: skip editing an input.
2. Step 3: skip the graph.
3. Step 6: stop after **Approve (simulated)** and say what the tracker would record.
4. Step 2: say "this decision was captured earlier" and skip to step 3. The capture live check stays empty; nothing else depends on it.

## If there is time, or a question calls for it

Show these after step 6. Switching the demo role clears the pilot markets filled in for step 6, and the Abstaining extra replaces the question.

- **Permissions (30 seconds).** Open tab 2 and note the **Decisions** count under the graph. Switch **Demo role** to **Analyst**: one decision fewer, and tab 6 says the Analyst demo role cannot approve or reject. Switch to **Restricted Executive**: one decision more than the Pricing Director sees, a board-only decision whose source the other two roles never see in search, the graph, citations or the recommendation. Say that role switching is a demo control, not security. Switch back to **Pricing Director**.
- **Evidence (20 seconds).** Scroll to the **Evidence explorer** at the bottom of the page and choose a source under **Open a source**. It shows the source in full, its mock reference (labelled as not a live link) and every decision and outcome that cites it, with the quoted line. Do not click a `[SRC-…]` citation while presenting: citations are links that open the source in a new browser tab, and that tab starts again at step 1. If you click one, close the new tab and carry on in the first.
- **Abstaining (20 seconds). Do this one last:** it replaces the question, and there is no button to put it back. In tab 2, replace the question with something unrelated, such as a payroll question. Search answers "insufficient verifiable evidence" and tab 5 does not recommend. To return, reload the page (⌘R): the demo question comes back, the role stays, and what the demo recorded stays. Do not paste the "Demo question: …" line from under the page title: its prefix and quote marks change the search and the recommendation does not come back.

## Likely questions

| Question | Answer |
|----------|--------|
| Is any of this real data? | No. Every company, person and number is synthetic and labelled as such. Nothing claims to reflect real freight economics. |
| Where does +$14.00M come from? | The formula in step 4. Every input is on screen and editable; four defaults cite fixture sources and the rest are labelled assumed for demo. |
| Is an AI model deciding? | No. Search, impact, scenarios and the recommendation are deterministic rules you can read. An optional Anthropic mode can reword the finished card into a short brief, checked against the card; it is off by default and was off in this demo. |
| Why is +7% excluded? | Its 1.6% churn loss exceeds the 1.2% guardrail (cited from SRC-014), and no successful precedent sits near that rate. |
| What if there is no precedent? | The app abstains: "insufficient verifiable evidence". It never fills a gap with an invented fact. |
| Does it change prices, send emails or open tickets? | No. It recommends and records. Approvals, KPIs and outcomes are simulated records typed into the demo. |
| Does it connect to Teams, SharePoint or Jira? | No. Sources are synthetic files, and links to them are mock links shown as text. |
| Who can see what? | Each source lists the roles that may read it. A decision appears only if the role can read every source it cites. A hidden record looks exactly like a missing one. |
| Could a document tell the app what to do? | No. Source text is shown as plain text and never followed as an instruction. |

## If something goes wrong

| Problem | What to do |
|---------|------------|
| "This transcript has already been captured." in step 2, or live checks are green before you start | The data is not at the baseline. Sidebar **Demo reset** → **Yes, reset** → **Start the demo from step 1**. Three clicks. |
| A citation link opened a second browser tab | Close it and return to the first tab; nothing was lost there. |
| A tab looks stale or half-drawn | Reload the page (⌘R). The role and everything saved stay. On-screen inputs return to their defaults: pick your step on the panel, and tick the exclusion in tab 3 again if you were past step 4. |
| The app will not start, or the reset fails | In a terminal: `python scripts/reset_demo.py`, then `streamlit run app.py`. |
| The laptop or projector fails | Play the backup recording, or open [demo_backup/slides.html](demo_backup/slides.html) (it works offline and keeps the synthetic-data label on every frame), and talk over it with the **Say** lines above. |

## Rehearsal record

| Date | How | Result |
|------|-----|--------|
| 2026-11-17 | The six steps clicked through in the running app on a scratch database, with the app in offline mode, as Pricing Director, starting from the reset button and ending with it. The three extras (Evidence, Permissions with the role selector, Abstaining and its way back) were clicked through separately after they were rewritten | See [QA_REPORT.md](QA_REPORT.md), "Stage 14 rehearsal" |
| 2026-11-17 (release gate) | A complete run in the browser against a server under a network guard, on fresh data. Every click was timed, and the frames shown in [demo_backup/slides.html](demo_backup/slides.html) were captured on the way. A second run checked that step 6 can be filled in before the demo starts | See [RELEASE_SCORECARD.md](RELEASE_SCORECARD.md), "Release-gate verification" |

Those runs checked that the clicks, labels, IDs and figures in this script match the app. They were driven by an automated browser, so they did not time a person speaking.

**Timing estimate.** The app answers each click in about 0.2–0.4 s, about 3 s across the 12 clicks (measured in the page from each click until Streamlit's running indicator cleared, polled every 50 ms, so the figures are approximate). The **Say** lines plus about half of the **Do** text read aloud come to about 340 words: roughly 2 to 2.5 minutes at 130–160 words a minute. That leaves about 2.5 minutes in the five for pointing, scrolling and pauses. Step 6 is the tight one, about 25–30 s of speech in a 30-second slot, which is why the checklist fills in its fields beforehand. These are estimates from word counts, not a timed rehearsal. Rehearse aloud with a stopwatch at least twice, with Wi-Fi off as the checklist says, and note your own times against the table at the top.
