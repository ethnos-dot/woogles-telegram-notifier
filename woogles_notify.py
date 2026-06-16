#!/usr/bin/env python3
"""Woogles -> Telegram notifier.

Polls your Woogles correspondence games and sends a Telegram message when:
  * it becomes your turn (with the time left on your clock),
  * a game you were playing finishes, or
  * analysis you requested on a recent game becomes ready.

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
import time
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
RECENT_URL = BASE + "/api/game_service.GameMetadataService/GetRecentGames"
DOC_URL = BASE + "/api/game_service.GameMetadataService/GetGameDocument"
ANALYSIS_STATUS_URL = BASE + "/api/analysis_service.AnalysisService/GetGamesAnalysisStatus"
GAME_LINK = BASE + "/game/{}"

STATE_FILE = os.environ.get("STATE_FILE", "state.json")
USER_AGENT = "woogles-telegram-notifier/1.0 (personal turn notifier)"
RECENT_GAMES_TO_SCAN = 25   # how many recent finished games to check for analysis
ANALYZED_HISTORY_CAP = 300  # cap on remembered analyzed game ids
REMINDER_INTERVAL_SECONDS = 2 * 60 * 60  # re-ping a standing "your turn" every 2h


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


def fmt_duration(ms):
    """Human-friendly clock remaining, e.g. '2d 4h', '5h 12m', '8m'."""
    if ms <= 0:
        return "time almost up!"
    secs = ms // 1000
    days, secs = divmod(secs, 86400)
    hours, secs = divmod(secs, 3600)
    mins, _ = divmod(secs, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


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


def time_bank_left(opener, gid, me):
    """Live time remaining on MY clock for a game where it's my turn.
    Returns a formatted string, or None if it can't be determined."""
    try:
        resp = post_json(opener, DOC_URL, {"gameId": gid})
        doc = resp.get("document") or resp
        players = doc.get("players") or []
        idx = next(
            (i for i, p in enumerate(players) if (p.get("nickname") or "").lower() == me.lower()),
            None,
        )
        timers = doc.get("timers") or {}
        remaining = pick(timers, "timeRemaining", "time_remaining") or []
        last_update = pick(timers, "timeOfLastUpdate", "time_of_last_update")
        if idx is None or idx >= len(remaining) or last_update is None:
            return None
        # It's my turn, so my clock is ticking: subtract elapsed since last update.
        elapsed = int(time.time() * 1000) - int(last_update)
        return fmt_duration(int(remaining[idx]) - elapsed)
    except Exception as exc:  # never let the clock lookup break a notification
        print("WARN time-bank lookup failed for", gid, "->", exc)
        return None


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


def check_analyses(opener, me, prev_analyzed, seed_only):
    """Detect newly-completed analyses among recent finished games.
    Returns (messages, updated_analyzed_list)."""
    try:
        recent = post_json(
            opener, RECENT_URL, {"username": me, "numGames": RECENT_GAMES_TO_SCAN, "offset": 0}
        )
        infos = pick(recent, "gameInfo", "game_info") or []
    except Exception as exc:
        print("WARN recent-games fetch failed ->", exc)
        return [], prev_analyzed
    id_to_opp = {}
    for g in infos:
        gid = pick(g, "gameId", "game_id")
        if gid:
            id_to_opp[gid] = opponent(g, me)
    if not id_to_opp:
        return [], prev_analyzed
    try:
        status = post_json(opener, ANALYSIS_STATUS_URL, {"gameIds": list(id_to_opp)})
        analyzed_now = pick(status, "analyzedGameIds", "analyzed_game_ids") or []
    except Exception as exc:
        print("WARN analysis-status fetch failed ->", exc)
        return [], prev_analyzed
    analyzed_now = [a for a in analyzed_now if a in id_to_opp]  # only recent ones
    prev_set = set(prev_analyzed or [])
    messages = []
    if not seed_only:
        for gid in analyzed_now:
            if gid not in prev_set:
                messages.append(
                    f"\U0001F4CA Analysis ready — your game vs {id_to_opp.get(gid, 'opponent')}"
                    f"\n{GAME_LINK.format(gid)}"
                )
    merged = list(analyzed_now) + [g for g in (prev_analyzed or []) if g not in set(analyzed_now)]
    return messages, merged[:ANALYZED_HISTORY_CAP]


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
    default = {"initialized": False, "games": {}, "analyzed": [], "analysisInitialized": False}
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default
    for k, v in default.items():
        data.setdefault(k, v)
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

    now = int(time.time())
    for g in games:
        gid = pick(g, "gameId", "game_id")
        if not gid:
            continue
        active_ids.add(gid)
        mine = my_turn(g, me)
        opp = opponent(g, me)
        last_update = pick(g, "lastUpdate", "last_update", default="")
        prev_game = prev.get(gid, {})
        was_on_turn = prev_game.get("onTurn", False)
        last_notified = prev_game.get("lastNotified")
        notified_at = None

        if mine:
            if first_run:
                # seed silently, but start the 2h reminder clock now
                notified_at = now
            else:
                fresh = not was_on_turn
                due = last_notified is None or (now - last_notified) >= REMINDER_INTERVAL_SECONDS
                if fresh or due:
                    head = "\U0001F3AF Your move vs " if fresh else "⏰ Still your move vs "
                    msg = head + opp
                    left = time_bank_left(opener, gid, me)
                    if left:
                        msg += f"\n⏳ {left} left on your clock"
                    messages.append(msg + f"\n{GAME_LINK.format(gid)}")
                    notified_at = now
                else:
                    notified_at = last_notified  # standing turn, reminder not due yet

        new_games[gid] = {
            "onTurn": bool(mine),
            "lastUpdate": last_update,
            "opp": opp,
            "lastNotified": notified_at,
        }

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

    # analysis-ready detection (seeded silently the first time it runs)
    seed_analysis = not state.get("analysisInitialized")
    analysis_msgs, analyzed = check_analyses(opener, me, state.get("analyzed", []), seed_analysis)
    if not seed_analysis:
        messages.extend(analysis_msgs)

    waiting = sum(1 for v in new_games.values() if v["onTurn"])
    if first_run:
        messages.append(
            f"✅ Woogles notifier is live. Tracking {len(new_games)} active "
            f"correspondence game(s); {waiting} waiting on you."
        )

    for text in messages:
        tg_send(token, chat_id, text)
        print("SENT:", text.splitlines()[0])

    save_state(
        {
            "initialized": True,
            "games": new_games,
            "analyzed": analyzed,
            "analysisInitialized": True,
        }
    )
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
