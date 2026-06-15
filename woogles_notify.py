#!/usr/bin/env python3
"""Woogles -> Telegram notifier.

Polls your Woogles correspondence games and sends a Telegram message when:
  * it becomes your turn in a game, or
  * a game you were playing finishes.

Designed to run statelessly on a schedule (e.g. GitHub Actions cron). State is
kept in state.json between runs so we only notify on *changes*.

Uses the Python standard library only -- no pip install required.

Modes:
  python woogles_notify.py                 # one poll cycle (default; used by cron)
  python woogles_notify.py --once          # same as above, explicit
  python woogles_notify.py --test          # send a test Telegram message and exit
  python woogles_notify.py --get-chat-id   # print chat ids that have messaged your bot
"""

import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

# Windows consoles default to cp1252, which crashes on emoji in our log lines.
# Force UTF-8 output everywhere; the Telegram payload is already UTF-8 JSON.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE = "https://woogles.io"
LOGIN_URL = BASE + "/api/user_service.AuthenticationService/Login"
ACTIVE_URL = BASE + "/api/game_service.GameMetadataService/GetActiveCorrespondenceGames"
META_URL = BASE + "/api/game_service.GameMetadataService/GetMetadata"
GAME_LINK = BASE + "/game/{}"

STATE_FILE = os.environ.get("STATE_FILE", "state.json")
USER_AGENT = "woogles-telegram-notifier/1.0 (personal turn notifier)"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def load_dotenv(path=".env"):
    """Minimal .env loader for local runs. Does nothing if the file is absent."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def pick(d, *keys, default=None):
    """Return the first present key. Connect-RPC serializes JSON as camelCase,
    but we accept snake_case too so the script is robust to either."""
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            return d[k]
    return default


def post_json(opener, url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with opener.open(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body.strip() else {}


# --------------------------------------------------------------------------- #
# Woogles
# --------------------------------------------------------------------------- #
def woogles_login(username, password):
    """Log in and return an opener whose cookie jar holds the session cookie."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    try:
        post_json(opener, LOGIN_URL, {"username": username, "password": password})
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")
        raise SystemExit(f"Woogles login failed (HTTP {err.code}): {detail}")
    return opener


def my_turn(game, me):
    """True/False if it's my move; None if it can't be determined."""
    idx = pick(game, "playerOnTurn", "player_on_turn")
    players = game.get("players") or []
    if idx is None or idx < 0 or idx >= len(players):
        return None
    nick = (players[idx].get("nickname") or "").lower()
    return nick == me.lower()


def opponent(game, me):
    for p in game.get("players") or []:
        if (p.get("nickname") or "").lower() != me.lower():
            return p.get("nickname") or "opponent"
    return "opponent"


def fetch_result(opener, gid, me):
    """Look up a finished game's outcome. Returns a label string, or None if the
    game does not actually appear finished (so we avoid false 'finished' alerts)."""
    try:
        meta = post_json(opener, META_URL, {"gameId": gid})
    except Exception:
        return None
    reason = pick(meta, "gameEndReason", "game_end_reason")
    if reason is None or str(reason).upper() in ("NONE", "GAME_END_REASON_NONE", "0"):
        return None  # not really over -- skip
    players = meta.get("players") or []
    scores = pick(meta, "scores", default=[]) or []
    winner = pick(meta, "winner")
    my_idx = next(
        (i for i, p in enumerate(players) if (p.get("nickname") or "").lower() == me.lower()),
        None,
    )
    score_str = ""
    if len(scores) == 2 and my_idx in (0, 1):
        score_str = f" ({scores[my_idx]}-{scores[1 - my_idx]})"
    if winner is None or my_idx is None or winner < 0:
        return f"ended{score_str}"
    return (f"you won{score_str} \U0001F389" if winner == my_idx else f"you lost{score_str}")


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def tg_send(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")
        raise SystemExit(f"Telegram send failed (HTTP {err.code}): {detail}")


def cmd_get_chat_id(token):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    chats = {}
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            who = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
            chats[chat["id"]] = who
    if not chats:
        print("No chats found. Send any message to your bot first, then re-run.")
        return
    print("Chats that have messaged your bot:")
    for cid, who in chats.items():
        print(f"  TELEGRAM_CHAT_ID={cid}   ({who})")


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"initialized": False, "games": {}}
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"initialized": False, "games": {}}
    data.setdefault("initialized", False)
    data.setdefault("games", {})
    return data


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")


# --------------------------------------------------------------------------- #
# main poll cycle
# --------------------------------------------------------------------------- #
def run_once(me, password, token, chat_id):
    opener = woogles_login(me, password)
    resp = post_json(opener, ACTIVE_URL, {})
    games = pick(resp, "gameInfo", "game_info", default=[]) or []

    state = load_state()
    first_run = not state.get("initialized")
    prev = state.get("games", {})
    new_games = {}
    messages = []
    active_ids = set()

    for g in games:
        gid = pick(g, "gameId", "game_id")
        if not gid:
            continue
        active_ids.add(gid)
        mine = my_turn(g, me)
        opp = opponent(g, me)
        last_update = pick(g, "lastUpdate", "last_update", default="")
        new_games[gid] = {"onTurn": bool(mine), "lastUpdate": last_update, "opp": opp}

        was_on_turn = prev.get(gid, {}).get("onTurn", False)
        if mine and not was_on_turn and not first_run:
            messages.append(f"\U0001F3AF Your move vs {opp}\n{GAME_LINK.format(gid)}")

    # games that left the active list -> likely finished
    if not first_run:
        for gid, info in prev.items():
            if gid not in active_ids:
                result = fetch_result(opener, gid, me)
                if result:
                    opp = info.get("opp", "opponent")
                    messages.append(
                        f"\U0001F3C1 Game vs {opp} finished — {result}\n{GAME_LINK.format(gid)}"
                    )

    waiting = sum(1 for v in new_games.values() if v["onTurn"])
    if first_run:
        messages.append(
            f"✅ Woogles notifier is live. Tracking {len(new_games)} active "
            f"correspondence game(s); {waiting} waiting on you."
        )

    for text in messages:
        tg_send(token, chat_id, text)
        print("SENT:", text.splitlines()[0])

    save_state({"initialized": True, "games": new_games})
    print(
        f"OK: {len(new_games)} active game(s), {waiting} on your turn, "
        f"{len(messages)} notification(s) sent."
    )


# --------------------------------------------------------------------------- #
def require_env(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SystemExit("Missing required env var(s): " + ", ".join(missing))


def main():
    load_dotenv()
    args = set(sys.argv[1:])
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if "--get-chat-id" in args:
        require_env("TELEGRAM_BOT_TOKEN")
        cmd_get_chat_id(token)
        return

    if "--test" in args:
        require_env("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
        tg_send(token, chat_id, "✅ Test from your Woogles notifier — it works!")
        print("Test message sent.")
        return

    require_env(
        "WOOGLES_USERNAME", "WOOGLES_PASSWORD", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"
    )
    run_once(
        os.environ["WOOGLES_USERNAME"],
        os.environ["WOOGLES_PASSWORD"],
        token,
        chat_id,
    )


if __name__ == "__main__":
    main()
