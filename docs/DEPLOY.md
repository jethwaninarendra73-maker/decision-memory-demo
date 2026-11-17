# Deploying the demo to Streamlit Community Cloud

**SYNTHETIC DATA — NOT A PREDICTION OR AUTHORIZED BUSINESS DECISION.**

This puts the demo at a web address you can send to a prospect. It is free, and it needs a GitHub account. The app is already prepared for it; you only do the steps below.

**What a visitor gets.** A password screen (if you set one), then the full app with their own fresh copy of the demo data. Two prospects using the link at the same time never see each other's captures, approvals or resets. A copy is deleted a few hours after its visitor leaves.

**What stays the same.** All data is synthetic, the app only advises, and it runs in offline mode: it calls no AI service and needs no API key.

## Before you start

- A GitHub account (free): https://github.com/signup
- About 15 minutes

## Step 1. Put the code on GitHub

1. On GitHub, click **New repository**.
2. Name it, for example `decision-memory-demo`. Choose **Private** unless you want anyone to be able to read the code. Do not add a README or licence; the project has them.
3. Click **Create repository**. GitHub shows the repository's address, like `https://github.com/YOUR-NAME/decision-memory-demo.git`.
4. In a terminal at the project folder, run these two commands, using your address:

```bash
git remote add origin https://github.com/YOUR-NAME/decision-memory-demo.git
```

```bash
git push -u origin main
```

GitHub may ask you to sign in. If it asks for a password, it wants a personal access token, not your account password; GitHub's page explains how to create one.

**What gets uploaded.** The code, the synthetic fixtures, the docs and the backup slides. Not uploaded (git ignores them): your `.env` with your API key, the `.venv` folder, `data/demo.db`, and the original concept document. A test in the suite checks that no key-shaped text is ever committed.

## Step 2. Create the app on Streamlit Community Cloud

1. Go to https://share.streamlit.io and sign in with GitHub. Allow it to see your repositories (including private ones, if yours is private).
2. Click **Create app**, then choose to deploy from a GitHub repository.
3. Fill in:
   - **Repository:** `YOUR-NAME/decision-memory-demo`
   - **Branch:** `main`
   - **Main file path:** `app.py`
   - **App URL:** pick a name, for example `decision-memory-demo`
4. Open **Advanced settings**:
   - **Python version:** the newest one offered that is 3.12 or later.
   - **Secrets:** paste the lines below, with a password you choose in place of the example. The password is typed only here, never into the code or a chat.

```toml
DM_PER_SESSION_DB = "true"
DM_ACCESS_PASSWORD = "choose-a-password-here"
LLM_MODE = "offline"
```

5. Click **Deploy**. The first start takes a few minutes while it installs the packages.

**Do not** put `ANTHROPIC_API_KEY` in the secrets. The hosted demo is meant to run offline; a key there would let anyone with the link spend your API credit.

## Step 3. Check it

1. Open the app's address. You should see the password screen with the yellow SYNTHETIC DATA label.
2. Enter your password. The app opens with "+5% pilot" and "+$14.00M" in the answer tiles.
3. Open the same address in a second browser (or a private window), sign in, and capture a decision there. Back in the first window, reload: the first copy does not show it.

## Step 4. Share it

Send the prospect the address and, separately, the password. Before a scheduled demo, open the link yourself a few minutes early: free apps go to sleep when nobody has used them for a while, and waking one takes a minute.

To change the password, edit the secrets in the app's settings on share.streamlit.io; the app restarts with the new one. To take the demo offline, delete the app there.

## Limits to know

- **The password is a shared door key, not user accounts.** Anyone you give it to can open the demo and switch to any demo role, including Restricted Executive. The data is synthetic, so nothing real is exposed.
- **Free hosting has limited memory and may sleep.** Fine for a few people at a time; for a real customer pilot, use your company's cloud behind company sign-in (see [HANDOFF.md](HANDOFF.md)).
- **Updates:** after `git push` of new commits, Community Cloud picks up the change and restarts the app.

## How it works (for whoever maintains it)

| Setting | Effect |
|---------|--------|
| `DM_PER_SESSION_DB = "true"` | Each browser session gets its own copy of the baseline in a temporary folder (`src/decision_memory/session_db.py`). **Demo reset** resets only that visitor's copy. |
| `DM_ACCESS_PASSWORD` | Shows a password screen first (`src/decision_memory/ui/access.py`), compared in constant time and never logged. |
| `LLM_MODE = "offline"` | The default anyway; written down so nobody turns the model on by accident. |

The app reads these from Streamlit's secrets or from environment variables. All three are off on a laptop. The app also creates the demo data itself if the database file is missing. `.streamlit/config.toml` names the browser address so the server makes no outside call at start-up, without limiting where it listens, so the host can reach it. The tests cover all of this (`tests/test_hosted.py`).
