# Woogles → Telegram notifier

Sends you a Telegram message when something happens in your **Woogles
correspondence games**:

- 🎯 **it becomes your turn**, or
- 🏁 **a game finishes** (with the result).

It runs free on **GitHub Actions** (a scheduled job in the cloud), so you don't
need a machine left on. State lives in `state.json` between runs, so you only get
pinged on *changes*.

> Scope: this covers **correspondence (async)** games — the kind where it's your
> turn hours or days later. Real-time games happen live in your browser, so a
> push notification doesn't apply. "Analysis requested/completed" is a separate
> Woogles service and can be added later.

---

## How it works

1. Logs into Woogles with your username/password → gets a session cookie.
2. Calls `GetActiveCorrespondenceGames` to list your active async games.
3. For each game checks `player_on_turn`; if that player is **you**, and it
   wasn't your turn last time, it sends a Telegram message linking to the game.
4. If a tracked game dropped off the active list, it looks up the result and
   sends a "finished" message.

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

A **private** repo is recommended. With the [GitHub CLI](https://cli.github.com/):

```powershell
cd E:\Claude\woogles-telegram-notifier
git init
git add .
git commit -m "Woogles Telegram notifier"
gh repo create woogles-telegram-notifier --private --source=. --push
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

After that it runs **every 30 minutes** automatically.

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

- **Free minutes (private repos):** ~2,000 min/month. At every-30-min that's
  ~1,440/month — comfortably under. **Don't** go below 30 min on a private repo
  or you'll blow the budget.
- **Want faster checks?** Make the repo **public** (Actions minutes are unlimited
  for public repos) and change the cron in
  [`.github/workflows/notify.yml`](.github/workflows/notify.yml) to `*/5 * * * *`.
  Your secrets stay encrypted either way; only `state.json` (game ids + turn
  flags) would be visible.
- **60-day rule:** GitHub auto-disables *scheduled* workflows after 60 days with
  no **human** commits (the bot's `state.json` commits don't count). Every couple
  months, push any commit or click **Enable** on the workflow to keep it alive.
- **Timing:** scheduled runs can be delayed 5–20 min under GitHub load. Fine for
  correspondence games.

---

## Tuning

- **Check frequency:** edit the `cron:` line in `.github/workflows/notify.yml`.
- **Message wording / emoji:** edit the `messages.append(...)` lines in
  `woogles_notify.py` (`run_once`).
- **If turn detection ever looks inverted** (says "your move" when it's the
  opponent's), the player index/order assumption is off — check the `my_turn()`
  function; the first run's Actions log prints what it saw.
