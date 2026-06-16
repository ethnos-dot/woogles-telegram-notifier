# Woogles → Telegram notifier

Sends you a Telegram message when something happens in your **Woogles
correspondence games**:

- 🎯 **it becomes your turn** (with the time left on your clock),
- ⏰ **a reminder every 2h** while a game is still waiting on your move,
- 🏁 **a game finishes** (with the result), or
- 📊 **analysis you requested becomes ready**.

It runs free on **GitHub Actions** (a scheduled job in the cloud), so you don't
need a machine left on. State lives in `state.json` between runs, so you only get
pinged on *changes*.

> Scope: turn/clock/finished alerts cover **correspondence (async)** games — the
> kind where it's your turn hours or days later. Analysis-ready alerts cover any
> recent finished game. Incoming **match requests** are *not* included: Woogles
> only delivers those over a live websocket, which a scheduled job can't poll.

---

## How it works

1. Logs into Woogles with your username/password → gets a session cookie.
2. Calls `GetActiveCorrespondenceGames` to list your active async games.
3. For each game checks `player_on_turn`; if that player is **you**, it sends a
   Telegram message (with the clock left from `GetGameDocument`) linking to the
   game — on the first flip to your turn, then again every 2h while it stays your
   turn (tracked per game via `lastNotified` in state; tune with
   `REMINDER_INTERVAL_SECONDS`).
4. If a tracked game dropped off the active list, it looks up the result and
   sends a "finished" message.
5. Checks recent finished games against `GetGamesAnalysisStatus`; when one newly
   has completed analysis, it sends an "analysis ready" message.

All with the Python standard library — **no `pip install`**.

---

## Setup (one time, ~10 minutes)

### 1. Create your Telegram bot

1. In Telegram, message **@BotFather** → send `/newbot` → follow the prompts.
2. Copy the **bot token** it gives you (looks like `123456789:AAH...`).
3. **Send any message to your new bot** (e.g. "hi") so it's allowed to message you.

### 2. Find your Telegram chat id

Easiest: open this URL in a browser (paste your token in):

```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```

Look for `"chat":{"id":123456789,...}` — that number is your `TELEGRAM_CHAT_ID`.

(Or, with Python installed, copy `.env.example` to `.env`, fill in the token, and
run `py woogles_notify.py --get-chat-id`.)

### 3. Put this folder in a GitHub repo

A **public** repo is recommended if you want frequent (10-minute) checks (public repos get
unlimited free Actions minutes; your secrets stay encrypted, only `state.json` is
visible). Use `--private` if you'd rather keep it private and check less often.
With the [GitHub CLI](https://cli.github.com/):

```powershell
cd E:\Claude\woogles-telegram-notifier
git init
git add .
git commit -m "Woogles Telegram notifier"
gh repo create woogles-telegram-notifier --public --source=. --push
```

(Or create an empty repo on github.com and `git remote add origin ... ; git push -u origin main`.)

### 4. Add your secrets to the repo

In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add these four (the values are never shown in logs):

| Secret name          | Value                                  |
| -------------------- | -------------------------------------- |
| `WOOGLES_USERNAME`   | your Woogles nickname                  |
| `WOOGLES_PASSWORD`   | your Woogles password                  |
| `TELEGRAM_BOT_TOKEN` | the token from BotFather               |
| `TELEGRAM_CHAT_ID`   | the chat id from step 2                |

### 5. Turn it on

Go to the **Actions** tab → enable workflows if prompted → open
**woogles-telegram-notifier** → **Run workflow** to trigger the first run
manually. You should get a "✅ Woogles notifier is live" message in Telegram.

After that it runs **every 10 minutes** automatically.

---

## Testing locally (optional)

You have Python via the `py` launcher. Copy `.env.example` to `.env`, fill it in, then:

```powershell
py woogles_notify.py --test          # sends a test Telegram message
py woogles_notify.py --get-chat-id   # prints chat ids that messaged your bot
py woogles_notify.py --once          # one real poll cycle (writes state.json)
```

`.env` is git-ignored, so your password never gets committed.

---

## Good to know (GitHub Actions specifics)

- **Minutes:** this repo runs every **10 minutes**, which relies on the
  **unlimited** Actions minutes you get on a **public** repo. If you switch it to
  private, drop the cron in
  [`.github/workflows/notify.yml`](.github/workflows/notify.yml) to `*/30 * * * *`
  (even 10-min on a private repo would blow past the ~2,000 free minutes/month).
  Either way your 4 secrets stay encrypted; only `state.json` (game ids,
  opponents, turn flags) is visible on a public repo.
- **60-day rule:** GitHub auto-disables *scheduled* workflows after 60 days with
  no **human** commits (the bot's `state.json` commits don't count). Every couple
  months, push any commit or click **Enable** on the workflow to keep it alive.
- **Timing:** GitHub heavily throttles `schedule` triggers — in practice runs can
  land **hours** apart, not on your cron. For tight, reliable timing use an
  external trigger (next section); manual/API dispatches are *not* throttled.

---

## Reliable timing (external trigger)

GitHub's `schedule` cron is best-effort and often runs hours late. To get
dependable ~10-minute checks, have a free external scheduler call GitHub's
**workflow-dispatch API** (those dispatches run immediately — no throttling).

**1. Create a fine-grained GitHub token**
- GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new.
- **Repository access:** only `woogles-telegram-notifier`.
- **Permissions:** Repository → **Actions: Read and write**. Nothing else.
- Set an expiry, generate, and copy the token (starts with `github_pat_`).

**2. Create the scheduled call at [cron-job.org](https://cron-job.org)** (free)
- New cronjob, schedule **every 10 minutes**.
- **URL:** `https://api.github.com/repos/ethnos-dot/woogles-telegram-notifier/actions/workflows/notify.yml/dispatches`
- **Method:** `POST`
- **Headers:**
  - `Authorization: Bearer <your github_pat_… token>`
  - `Accept: application/vnd.github+json`
  - `Content-Type: application/json`
- **Body:** `{"ref":"main"}`
- Save. A successful call returns HTTP **204**, and a `workflow_dispatch` run
  appears in the Actions tab within seconds.

The built-in `schedule:` trigger stays as a free backup; overlapping runs are
serialized by the workflow's `concurrency` group, and state-dedup means no double
pings. Paste the token into cron-job.org only — never commit it.

---

## Tuning

- **Check frequency:** edit the `cron:` line in `.github/workflows/notify.yml`.
- **Message wording / emoji:** edit the `messages.append(...)` lines in
  `woogles_notify.py` (`run_once`).
- **If turn detection ever looks inverted** (says "your move" when it's the
  opponent's), the player index/order assumption is off — check the `my_turn()`
  function; the first run's Actions log prints what it saw.
