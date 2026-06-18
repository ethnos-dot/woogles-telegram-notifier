# Woogles → Telegram notifier

A self-hostable bot that sends **you** a Telegram message when something happens
in **your** [Woogles](https://woogles.io) correspondence games:

- 🎯 **it becomes your turn** (with the time left on your clock),
- ⏰ **a reminder every 2h** while a game is still waiting on your move,
- 🏁 **a game finishes** (with the result), or
- 📊 **analysis you requested becomes ready**.

It runs free on **GitHub Actions** — no server, nothing to leave on. Each person
runs **their own copy** with **their own** Woogles account and Telegram bot;
nothing is shared and there's no central service.

> ⚠️ **Your Woogles API key is like a password** — it authenticates as you across
> the whole API. It stays in **your own** repo's encrypted Secrets and is never
> shared with anyone. (Woogles' own guidance: keep your API key secret.) That's
> exactly why this is a *self-host* template rather than a hosted sign-up service.

---

## Use this template

Click **“Use this template” → Create a new repository** at the top of this repo
to get your own independent copy (no shared history). Then follow Setup below in
*your* copy.

> *Repo owner:* to show that button, enable **Settings → General → Template
> repository**.

---

## Setup (one time, ~10 minutes)

### 1. Create your Telegram bot
In Telegram, message **@BotFather** → `/newbot` → follow the prompts → copy the
**bot token** (looks like `123456789:AA…`). Then **send your new bot any message**
(e.g. “hi”) so it’s allowed to message you.

### 2. Find your Telegram chat id
Open this in a browser (paste your token in):
```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```
Find `"chat":{"id":123456789,…}` — that number is your `TELEGRAM_CHAT_ID`.

### 3. Get your Woogles API key
On woogles.io: **Settings → API → Generate API key**, click the eye icon, and
copy it. It’s revocable (regenerate any time) and is **not** your password.

### 4. Add your secrets
In *your* repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add:

| Secret name          | Value                                            |
| -------------------- | ------------------------------------------------ |
| `WOOGLES_USERNAME`   | your Woogles nickname                            |
| `WOOGLES_API_KEY`    | the API key from step 3                          |
| `TELEGRAM_BOT_TOKEN` | the token from step 1                            |
| `TELEGRAM_CHAT_ID`   | the chat id from step 2                          |

*(Prefer not to use an API key? You can instead set `WOOGLES_PASSWORD` — less
safe, since it stores your password. The API key is recommended.)*

### 5. Make the repo public (for free 10-min checks)
Public repos get **unlimited** Actions minutes, so the every-10-minutes schedule
is free. Your secrets stay encrypted regardless — only the code is visible.
(Prefer private? Change the cron to `*/30 * * * *` — see Notes.)

### 6. Turn it on
**Actions** tab → enable workflows if prompted → open **woogles-telegram-notifier**
→ **Run workflow**. You’ll get a “✅ notifier is live” message listing how many
games are waiting on you. After that it runs automatically.

---

## How it works

1. Authenticates to Woogles with your API key (`X-Api-Key` header).
2. `GetActiveCorrespondenceGames` lists your active async games; if
   `player_on_turn` is **you** it messages you (with the clock from
   `GetGameDocument`) — once on the flip to your turn, then every 2h while it
   stays your turn.
3. A tracked game that drops off the active list → looks up the result and sends
   a “finished” message.
4. `GetGamesAnalysisStatus` on your recent games → “analysis ready” when one
   newly completes.

State (what it’s already told you) is kept in the **GitHub Actions cache**, not
committed to the repo — so the repo stays clean and you only ever get pinged on
*changes*. Standard library only — **no `pip install`**.

> **Scope:** correspondence (async) games for turn/clock/finished; any recent
> finished game for analysis. Incoming **match requests** aren’t included —
> Woogles only delivers those over a live websocket, which a scheduled job
> can’t poll.

---

## Reliable timing (optional but recommended)

GitHub’s `schedule` cron is best-effort and often runs **hours** late. For
dependable ~10-minute checks, have a free scheduler call GitHub’s
workflow-dispatch API (manual dispatches aren’t throttled):

1. **Fine-grained GitHub token** — GitHub → Settings → Developer settings →
   **Fine-grained tokens** → Generate. Repository access: **only this repo**.
   Permissions: **Actions → Read and write**.
2. **A free job at [cron-job.org](https://cron-job.org)**, every **10 minutes**:
   - **POST** to (replace `OWNER/REPO` with yours):
     `https://api.github.com/repos/OWNER/REPO/actions/workflows/notify.yml/dispatches`
   - Headers: `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`,
     `Content-Type: application/json`
   - Body: `{"ref":"main"}`
   - A working call returns HTTP **204**. Paste the token into cron-job.org only.

---

## Testing locally (optional)
With Python installed, copy `.env.example` to `.env`, fill it in, then:
```
python woogles_notify.py --test          # send a test Telegram message
python woogles_notify.py --get-chat-id   # print chat ids that messaged your bot
python woogles_notify.py --once          # one real poll cycle
```
`.env` and `state.json` are git-ignored.

---

## Notes & limits
- **Minutes:** every-10-min relies on the unlimited Actions minutes of a **public**
  repo. On a private repo use `*/30 * * * *` (10-min would exceed the ~2,000 free
  minutes/month).
- **60-day rule:** GitHub auto-disables *scheduled* workflows after 60 days with
  no commits. Push any commit, or rely on the external trigger above, to keep it
  alive.
- **Revoking access:** regenerate your key at Woogles **Settings → API** (the old
  one stops working immediately), then update the `WOOGLES_API_KEY` secret.

## Tuning
- **Check frequency:** the `cron:` line in `.github/workflows/notify.yml`.
- **Reminder interval:** `REMINDER_INTERVAL_SECONDS` in `woogles_notify.py`.
- **Message wording:** the `messages.append(...)` lines in `run_once`.
