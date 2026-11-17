# Demo checklist

**SYNTHETIC DATA — NOT A PREDICTION OR AUTHORIZED BUSINESS DECISION.**

Use with [DEMO_SCRIPT.md](DEMO_SCRIPT.md). Commands run from the repository root on macOS.

## The day before

- [ ] Set up on the laptop you will present from:
  ```bash
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  ```
- [ ] Run the tests; they need no network and no API key:
  ```bash
  python -m pytest -q
  ```
- [ ] Rehearse aloud with a stopwatch, twice, following the script. Reset between runs with the sidebar **Demo reset** button. Write your time for each step next to the script's table.
- [ ] Decide what you will cut if you run long (the script lists the order).
- [ ] Record a backup: a screen recording of one clean run, with your narration, saved on the laptop and on a second device. Until you have one, the slides in [demo_backup/slides.html](demo_backup/slides.html) are the fallback: one complete run, frame by frame, each under the synthetic-data label. Copy the whole `demo_backup` folder to the second device too, and open `slides.html`, never the loose frames.
- [ ] Check the room: projector resolution, and that the browser at 100% zoom shows the guide panel and the tabs without horizontal scrolling. Zoom the browser, not the laptop display.

## Thirty minutes before

- [ ] Laptop on power, notifications off (Focus on), other apps closed.
- [ ] Turn Wi-Fi off, then reload the page and check that it draws fully (the graph in tab 2 included). A full demo run was checked with the server under a network guard: the server tried no outside call and the page loaded everything from this laptop. Turning Wi-Fi off was not part of that check, so confirm it once in your own rehearsal.
- [ ] Leave `LLM_MODE` unset (or `offline`). The script assumes the optional Anthropic mode is off; the sidebar must read **Mode: `offline`**.
- [ ] Start the app:
  ```bash
  source .venv/bin/activate
  python scripts/reset_demo.py
  streamlit run app.py
  ```
- [ ] Open http://localhost:8501 in one browser window, full screen. Close other tabs of the app: a second tab keeps its own typed inputs.

## Right before you speak

- [ ] Sidebar **Demo role** is **Pricing Director**, and **Simple view** is on.
- [ ] Sidebar **Guided demo** is on, and the panel shows **Step 1 of 6 · Hook**.
- [ ] The data is at the baseline. If in doubt: **Demo reset** → **Yes, reset** → **Start the demo from step 1**.
- [ ] Fill in step 6 now, so it is three clicks on stage: open tab **6 · Approve and track**, type a fictional name in **Your name**, choose **France** and **Italy** as pilot markets, then click the tab **1 · Capture a decision** to leave tab 6. (Clicking **1 · Hook** on the guide does not leave tab 6, because the guide is already at step 1.) Both fields keep their values through steps 1–5 (checked in the app). A reset clears both. Switching the **Demo role** clears the pilot markets, because only approver roles see that field: show the role extras after step 6, or choose the markets again.
- [ ] Step 2's live check reads "No decision captured in the app yet." (click step 2 to look, then go back to step 1).
- [ ] The yellow **SYNTHETIC DATA** label is visible at the top.
- [ ] The backup recording, or [demo_backup/slides.html](demo_backup/slides.html), is one click away.
- [ ] A stopwatch or the laptop clock is in view.

## During the demo

- [ ] Say that the data is synthetic and that the app only recommends.
- [ ] Move with **Next step ▶**; it opens the right tab.
- [ ] Read the "Where does +$14.00M come from?" line from the guide panel, not from memory.
- [ ] Watch the live checks: a green tick means the app holds what the step needs.
- [ ] Do not click `[SRC-…]` citation links: they open a second browser tab. To show a source, use **Open a source** in the Evidence explorer at the bottom of the page.
- [ ] If anything looks wrong, use the recovery table at the end of the script. Do not debug on stage.

## After the demo

- [ ] **Demo reset** so the next run starts clean.
- [ ] Stop the app with `Ctrl+C` in the terminal.
- [ ] Note any question you could not answer, for the hand-off notes.

## What the reset does

- The sidebar **Demo reset** button asks for confirmation, then deletes everything created in the app (captured decisions, approvals, pilot records, outcomes and their audit events) for every demo role, and rebuilds the synthetic baseline from the fixture files. It also clears what was typed in that browser tab. Fixture files are not changed.
- It is the same reset as `python scripts/reset_demo.py`. Use the script if the app will not start.
- It only works on a database this app created, and it cannot be undone.
- A reset leaves no audit event: the rebuilt database is the baseline, with only the seed event in its audit trail. The app's log records that a reset ran.
