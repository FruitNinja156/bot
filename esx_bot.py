#!/usr/bin/env python3
"""
esx_bot.py - fetch listed-security prices from the Ethiopian Securities Exchange (esx.et).

How it works (reverse-engineered from the homepage ticker, Sep 2026):
  1. GET https://esx.et/ and pull a short-lived WordPress nonce out of the inline ticker script.
  2. POST action=esx_get_ticker&nonce=<nonce> to https://esx.et/wp-admin/admin-ajax.php
  3. Response: {"success": true, "data": [{"symbol","price","change"(%),"arrow","class"}, ...]}
No login or cookies are needed. Nonces expire (typically 12-24h), so we refresh on a 403.

Usage:
  python esx_bot.py                      # print current prices
  python esx_bot.py --symbols AWAB WGBX  # only these symbols
  python esx_bot.py --csv prices.csv     # also append a timestamped snapshot to a CSV
  python esx_bot.py --json               # machine-readable output
  python esx_bot.py --watch 30 --csv prices.csv   # poll every 30 minutes
  python esx_bot.py --post               # emoji post text, next template in the rotation
  python esx_bot.py --post --template 2  # emoji post text using a specific template
  python esx_bot.py --send               # build the post AND send it to Telegram
  python esx_bot.py --send linkedin      # ...to LinkedIn
  python esx_bot.py --send x             # ...to X (Twitter), in a compact 280-character format
  python esx_bot.py --send all           # ...to every platform whose token is set

Requires: pip install requests
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

BASE = "https://esx.et"
AJAX = f"{BASE}/wp-admin/admin-ajax.php"
HEADERS = {"User-Agent": "esx-personal-price-bot/0.1 (personal use)"}

# ---------------------------------------------------------------------------
# >>> API KEY / BOT TOKEN GOES HERE <<<
# Do NOT paste the token into this file. It is read from environment variables:
#   TELEGRAM_BOT_TOKEN  - the token @BotFather gives you, e.g. 123456789:AAH...
#   TELEGRAM_CHAT_ID    - where to post: a channel like @my_esx_channel, or a numeric chat id
# On GitHub Actions these come from the repository's Secrets (see README.md).
# On your own computer:  export TELEGRAM_BOT_TOKEN="..."  (PowerShell: $env:TELEGRAM_BOT_TOKEN="...")
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# >>> LINKEDIN ACCESS TOKEN GOES HERE (also via environment / GitHub Secrets) <<<
#   LINKEDIN_ACCESS_TOKEN - from LinkedIn's OAuth token generator (scopes: openid profile w_member_social).
#                           EXPIRES AFTER 60 DAYS - generate a new one and update the secret.
#   LINKEDIN_AUTHOR_URN   - optional. Leave unset to post as yourself (looked up automatically).
#                           Set to urn:li:organization:<id> only if LinkedIn approves you for page posting.
LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN")
LINKEDIN_AUTHOR_URN = os.environ.get("LINKEDIN_AUTHOR_URN")
LINKEDIN_VERSION = os.environ.get("LINKEDIN_VERSION", "202606")  # LinkedIn API version, YYYYMM

# >>> X (TWITTER) KEYS GO HERE (also via environment / GitHub Secrets) <<<
# All four come from your app in the X Developer Console ("Keys and tokens"). They don't expire.
# Set the app's permissions to "Read and write" BEFORE generating the access token + secret.
X_API_KEY = os.environ.get("X_API_KEY")                      # a.k.a. Consumer Key
X_API_SECRET = os.environ.get("X_API_SECRET")                # a.k.a. Consumer Secret
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = os.environ.get("X_ACCESS_TOKEN_SECRET")
# 280 for standard accounts. With X Premium you can set this higher (e.g. 4000) to avoid splitting.
X_MAX_CHARS = int(os.environ.get("X_MAX_CHARS") or 280)

# The nonce sits in the same inline script as the action name; match either ordering.
NONCE_PATTERNS = [
    re.compile(r"esx_get_ticker[\s\S]{0,800}?nonce['\"]?\s*[:=]\s*['\"]([a-f0-9]+)['\"]", re.I),
    re.compile(r"nonce['\"]?\s*[:=]\s*['\"]([a-f0-9]+)['\"][\s\S]{0,800}?esx_get_ticker", re.I),
]


class ESXError(Exception):
    pass


class ESXClient:
    def __init__(self, timeout=20):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.timeout = timeout
        self._nonce = None

    def _refresh_nonce(self):
        r = self.s.get(BASE + "/", timeout=self.timeout)
        r.raise_for_status()
        for pat in NONCE_PATTERNS:
            m = pat.search(r.text)
            if m:
                self._nonce = m.group(1)
                return
        raise ESXError("Couldn't find the ticker nonce on the homepage - the site layout may have changed.")

    def get_prices(self):
        """Return a list of dicts: {symbol, price (float), change_pct (float)}."""
        for attempt in range(2):
            if self._nonce is None:
                self._refresh_nonce()
            r = self.s.post(AJAX, data={"action": "esx_get_ticker", "nonce": self._nonce},
                            timeout=self.timeout)
            if r.status_code == 403 and attempt == 0:
                self._nonce = None  # expired nonce - fetch a fresh one and retry once
                continue
            r.raise_for_status()
            payload = json.loads(r.text.strip())  # response has leading blank lines
            if not payload.get("success"):
                raise ESXError(f"ESX returned an error: {payload.get('data')}")
            return [
                {
                    "symbol": row["symbol"],
                    "price": float(row["price"]),
                    "change_pct": float(row.get("change") or 0),
                }
                for row in payload["data"]
            ]
        raise ESXError("Request rejected even with a fresh nonce.")


def print_table(rows, fetched_at):
    print(f"ESX prices  -  {fetched_at.astimezone().strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"{'Symbol':<8}{'Price (ETB)':>14}{'Change':>10}")
    print("-" * 32)
    for r in rows:
        arrow = "▲" if r["change_pct"] > 0 else "▼" if r["change_pct"] < 0 else " "
        print(f"{r['symbol']:<8}{r['price']:>14,.2f}{arrow:>3}{r['change_pct']:>+6.2f}%")


# ---------------------------------------------------------------------------
# Post formatting
# ---------------------------------------------------------------------------

# Optional display names, shown as "AWAB (Awash Bank)". Empty = tickers only (current choice).
# To add one later: "AWAB": "Awash Bank",
NAMES = {}

# Symbols that are interest rates, not share prices. They get their own line ("🏦 IBMM ... 13.00%")
# and are left out of the gainers/decliners lists, the counts and the top gainer/loser.
RATES = {
    "IBMM": "interbank rate",
}

UP, DOWN, FLAT = "🟢", "🔴", "⚪"

# Each template gets: {date}, {body}, {ups}, {downs}, {flats}, {top_line}.
# The bot cycles through these in order, one per post, then starts over.
# Add, remove or edit freely; just keep {body} somewhere in each one.
TEMPLATES = [
    "📊 ESX Market Update | {date}\n\n{body}\n\n{top_line}\n🟢 {ups} up · 🔴 {downs} down · ⚪ {flats} flat",

    "🇪🇹 Ethiopian Securities Exchange\n🗓️ {date}\n\n{body}\n\n{top_line}",

    "💹 Today on the ESX ({date})\n\n{body}\n\nAdvancers: {ups} | Decliners: {downs} | Unchanged: {flats}",

    "🔔 ESX Price Check\n\n{body}\n\n{top_line}\n📅 {date}",

    "📈📉 How ESX stocks are moving | {date}\n\n{body}\n\n{top_line}",
]

# Time zone for the date/time shown in posts. ESX trades on Addis Ababa time (EAT, UTC+3).
POST_TZ = ZoneInfo("Africa/Addis_Ababa")
POST_TZ_LABEL = "EAT"

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".esx_post_state.json")


def _status(pct):
    return UP if pct > 0 else DOWN if pct < 0 else FLAT


def format_security(r):
    name = NAMES.get(r["symbol"])
    label = f"{r['symbol']} ({name})" if name else r["symbol"]
    pct = r["change_pct"]
    move = "0.00%" if pct == 0 else f"{'▲' if pct > 0 else '▼'} {pct:+.2f}%"
    return f"{_status(pct)} {label}\n     ETB {r['price']:,.2f}  {move}"


SECTIONS = [  # (heading, which securities go under it)
    ("📈 GAINERS", lambda pct: pct > 0),
    ("➖ UNCHANGED", lambda pct: pct == 0),
    ("📉 DECLINERS", lambda pct: pct < 0),
]


def _stocks(rows):
    return [r for r in rows if r["symbol"] not in RATES]


def _body(rows):
    """Grouped list: gainers, unchanged, decliners (empty groups left out), then rate lines."""
    ordered = sorted(_stocks(rows), key=lambda r: -r["change_pct"])
    blocks = []
    for heading, belongs in SECTIONS:
        group = [r for r in ordered if belongs(r["change_pct"])]
        if group:
            blocks.append(heading + "\n\n" + "\n\n".join(format_security(r) for r in group))
    for r in rows:
        if r["symbol"] in RATES:
            blocks.append(f"🏦 {r['symbol']} ({RATES[r['symbol']]}): {r['price']:.2f}%")
    return "\n\n".join(blocks)


def _top_line(rows):
    rows = _stocks(rows)
    movers = [r for r in rows if r["change_pct"] != 0]
    if not movers:
        return "😴 No price changes today."
    best = max(rows, key=lambda r: r["change_pct"])
    worst = min(rows, key=lambda r: r["change_pct"])
    parts = []
    if best["change_pct"] > 0:
        parts.append(f"🚀 Top gainer: {best['symbol']} {best['change_pct']:+.2f}%")
    if worst["change_pct"] < 0:
        parts.append(f"⚠️ Top loser: {worst['symbol']} {worst['change_pct']:+.2f}%")
    return "\n".join(parts)


def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass  # rotation/dedupe just won't persist; posting still works


def _next_template_index():
    """Read the rotation position, advance it, and save it for next time."""
    state = _load_state()
    idx = state.get("next", 0) % len(TEMPLATES)
    state["next"] = (idx + 1) % len(TEMPLATES)
    _save_state(state)
    return idx


def _snapshot(rows):
    return sorted([r["symbol"], r["price"], r["change_pct"]] for r in rows)


def prices_unchanged_since_last_post(rows):
    return _load_state().get("last_posted") == _snapshot(rows)


def remember_posted(rows):
    state = _load_state()
    state["last_posted"] = _snapshot(rows)
    _save_state(state)


def find_new_listings(rows):
    """Securities the bot has never seen before. The very first time this runs, it just records
    what's listed now (so existing companies aren't announced as new) and returns nothing."""
    known = _load_state().get("known_symbols")
    if known is None:
        remember_symbols(rows)
        return []
    return [r for r in rows if r["symbol"] not in set(known)]


def remember_symbols(rows):
    """Add these tickers to the known list. Tickers are never removed, so one that briefly
    drops off the ESX ticker isn't announced again when it comes back."""
    state = _load_state()
    state["known_symbols"] = sorted(set(state.get("known_symbols") or []) | {r["symbol"] for r in rows})
    _save_state(state)


def _post_datetime(dt):
    """e.g. 'Thu 24 Sep 2026 · 12:00 PM EAT'"""
    local = dt.astimezone(POST_TZ)
    hour = local.strftime("%I").lstrip("0")
    return f"{local:%a %d %b %Y} · {hour}:{local:%M %p} {POST_TZ_LABEL}"


# Session labels: the first line of each post, so readers know what kind of update it is.
# (full label, short label for X)
SESSIONS = {
    "preopen": ("🌅 PRE-OPENING · Last close, before trading starts", "🌅 PRE-OPENING"),
    "opening": ("🔔 OPENING · First prices of the day", "🔔 OPENING"),
    "hourly": ("⏱️ HOURLY UPDATE", "⏱️ HOURLY"),
    "closing": ("🏁 CLOSING · Final prices for the day", "🏁 CLOSING"),
}


def build_post(rows, fetched_at, template_index=None, session=None):
    """Return the post text. Uses the next template in rotation unless one is given.
    session (preopen/opening/hourly/closing) adds a label as the first line."""
    if template_index is None:
        template_index = _next_template_index()
    stocks = _stocks(rows)
    post = TEMPLATES[template_index % len(TEMPLATES)].format(
        date=_post_datetime(fetched_at),
        body=_body(rows),
        ups=sum(r["change_pct"] > 0 for r in stocks),
        downs=sum(r["change_pct"] < 0 for r in stocks),
        flats=sum(r["change_pct"] == 0 for r in stocks),
        top_line=_top_line(rows),
    ).strip()
    if session:
        return SESSIONS[session][0] + "\n\n" + post
    return post


# New-listing announcement: sent as its own message, before the regular update, the first time
# a ticker appears. Edit the wording freely. Fields: {symbol} {price} {move} {date}
LISTING_TEMPLATE = (
    "🆕 NEW LISTING ON ESX\n\n"
    "🎉 {symbol} has joined the Ethiopian Securities Exchange!\n\n"
    "💰 First price: ETB {price}{move}\n"
    "📅 {date}\n\n"
    "Welcome to the market! 🇪🇹"
)
LISTING_TEMPLATE_X = "🆕 NEW LISTING ON ESX: {symbol} has joined! 🎉 First price ETB {price}{move} · {date}"


def build_listing_posts(new_rows, fetched_at):
    """One announcement per new security: {"full": [texts], "x": [texts]}."""
    full, x = [], []
    for r in new_rows:
        pct = r["change_pct"]
        fields = dict(symbol=r["symbol"], price=f"{r['price']:,.2f}", date=_post_datetime(fetched_at),
                      move="" if pct == 0 else f"  {'▲' if pct > 0 else '▼'} {pct:+.2f}%")
        full.append(LISTING_TEMPLATE.format(**fields))
        x.append(LISTING_TEMPLATE_X.format(**fields))
    return {"full": full, "x": x}


# Short rotating headers for X, where every character counts. {date} is filled in.
X_HEADERS = [
    "📊 ESX Update · {date}",
    "🇪🇹 ESX Prices · {date}",
    "💹 Today on the ESX · {date}",
    "🔔 ESX Price Check · {date}",
    "📈📉 ESX Movers · {date}",
]


def x_length(text):
    """Approximate X's weighted count: basic Latin/punctuation = 1, emoji and most others = 2.
    Errs on the high side so posts are never rejected for length."""
    n = 0
    for ch in text:
        c = ord(ch)
        light = c <= 0x10FF or 0x2000 <= c <= 0x200D or 0x2010 <= c <= 0x201F or 0x2032 <= c <= 0x2037
        n += 1 if light else 2
    return n


def build_x_posts(rows, fetched_at, template_index, session=None):
    """Compact version for X. Returns a list of posts, split into (1/2), (2/2)... if too long."""
    ordered = sorted(rows, key=lambda r: -r["change_pct"])
    lines = []
    for r in ordered:
        pct = r["change_pct"]
        pct_txt = "0%" if pct == 0 else f"{pct:+.2f}%"
        lines.append(f"{_status(pct)} {r['symbol']} {r['price']:,.2f} {pct_txt}")
    header = X_HEADERS[template_index % len(X_HEADERS)].format(date=_post_datetime(fetched_at))
    if session:
        header = SESSIONS[session][1] + " · " + header

    # Pack lines into as few posts as possible (reserving room for a " (1/2)" marker).
    posts, current = [], []
    for line in lines:
        candidate = "\n".join([header + " (9/9)", ""] + current + [line])
        if current and x_length(candidate) > X_MAX_CHARS:
            posts.append(current)
            current = []
        current.append(line)
    posts.append(current)

    if len(posts) == 1:
        return ["\n".join([header, ""] + posts[0])]
    return ["\n".join([f"{header} ({i}/{len(posts)})", ""] + chunk) for i, chunk in enumerate(posts, 1)]


def send_telegram(text):
    """Post text to the Telegram chat/channel configured above."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise ESXError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set to use --send.")
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4096], "disable_web_page_preview": True},
        timeout=20,
    )
    try:
        ok = r.json().get("ok")
        desc = r.json().get("description")
    except ValueError:
        ok, desc = False, r.text[:200]
    if not ok:
        # Never echo the URL: it contains the token.
        raise ESXError(f"Telegram rejected the message: {desc}")


# LinkedIn post text uses "little text format": these characters must be backslash-escaped,
# otherwise the post is rejected or cut off (our posts use ( ) and | for example).
_LI_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")


def _linkedin_escape(text):
    return _LI_RESERVED.sub(r"\\\1", text)


def send_linkedin(text):
    """Publish text as a LinkedIn post."""
    if not LINKEDIN_ACCESS_TOKEN:
        raise ESXError("LINKEDIN_ACCESS_TOKEN must be set to post to LinkedIn.")
    auth = {"Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}"}

    author = LINKEDIN_AUTHOR_URN
    if not author:
        r = requests.get("https://api.linkedin.com/v2/userinfo", headers=auth, timeout=20)
        if r.status_code == 401:
            raise ESXError("LinkedIn token is expired or invalid - generate a new one (it lasts 60 days) "
                           "and update the LINKEDIN_ACCESS_TOKEN secret.")
        r.raise_for_status()
        author = f"urn:li:person:{r.json()['sub']}"

    r = requests.post(
        "https://api.linkedin.com/rest/posts",
        headers={**auth, "LinkedIn-Version": LINKEDIN_VERSION,
                 "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"},
        json={
            "author": author,
            "commentary": _linkedin_escape(text[:3000]),  # LinkedIn's post length limit
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                             "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        },
        timeout=20,
    )
    if r.status_code == 401:
        raise ESXError("LinkedIn token is expired or invalid - generate a new one (it lasts 60 days) "
                       "and update the LINKEDIN_ACCESS_TOKEN secret.")
    if r.status_code not in (200, 201):
        try:
            msg = r.json().get("message", r.text[:200])
        except ValueError:
            msg = r.text[:200]
        raise ESXError(f"LinkedIn rejected the post ({r.status_code}): {msg}")


def _x_configured():
    return all([X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET])


def _x_create(auth, text, reply_to=None):
    body = {"text": text}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": reply_to}
    return requests.post("https://api.x.com/2/tweets", json=body, auth=auth, timeout=20)


def _x_error(r):
    try:
        j = r.json()
        return (j.get("detail") or j.get("title") or str(j)[:200]).rstrip(".")
    except ValueError:
        return r.text[:200]


def send_x(parts):
    """Post to X. Multiple parts are threaded; if X refuses the reply, they're posted separately."""
    if not _x_configured():
        raise ESXError("X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN and X_ACCESS_TOKEN_SECRET must all be set.")
    from requests_oauthlib import OAuth1  # only needed for X
    auth = OAuth1(X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET)

    previous_id = None
    for i, text in enumerate(parts):
        r = _x_create(auth, text, reply_to=previous_id)
        if previous_id and r.status_code == 403:
            r = _x_create(auth, text)  # thread reply refused: post it on its own instead
        if r.status_code == 401:
            raise ESXError("X rejected the keys (401). Re-check all four X secrets.")
        if r.status_code == 402:
            raise ESXError("X says payment required (402): add credits in the X Developer Console.")
        if r.status_code == 403 and i == 0:
            raise ESXError(f"X refused the post (403): {_x_error(r)}. If it mentions permissions, set the "
                           "app to 'Read and write' and regenerate the access token + secret.")
        if r.status_code not in (200, 201):
            raise ESXError(f"X rejected post {i + 1}/{len(parts)} ({r.status_code}): {_x_error(r)}")
        previous_id = r.json()["data"]["id"]


# name -> (send function taking the built posts, "is it configured?" check)
PLATFORMS = {
    "telegram": (lambda p: send_telegram(p["full"]), lambda: bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)),
    "linkedin": (lambda p: send_linkedin(p["full"]), lambda: bool(LINKEDIN_ACCESS_TOKEN)),
    "x": (lambda p: send_x(p["x"]), _x_configured),
}


def send_everywhere(posts, targets):
    """Send to each target; one platform failing doesn't stop the others."""
    if targets == ["all"]:
        targets = [name for name, (_, configured) in PLATFORMS.items() if configured()]
        if not targets:
            raise ESXError("No platforms configured - set the Telegram, LinkedIn and/or X secrets.")
    failures, posted = [], []
    for name in targets:
        if name not in PLATFORMS:
            failures.append(f"{name}: unknown platform (use telegram, linkedin, x or all)")
            continue
        try:
            PLATFORMS[name][0](posts)
            posted.append(name)
            print(f"✅ Posted to {name.upper() if name == 'x' else name.capitalize()}.")
        except (requests.RequestException, ESXError) as e:
            print(f"❌ {name.upper() if name == 'x' else name.capitalize()} failed: {e}", file=sys.stderr)
            failures.append(f"{name}: {e}")
    if failures:
        err = ESXError("Some posts failed -> " + " | ".join(failures))
        err.posted_somewhere = bool(posted)
        raise err


def append_csv(path, rows, fetched_at):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["fetched_at_utc", "symbol", "price_etb", "change_pct"])
        ts = fetched_at.isoformat(timespec="seconds")
        for r in rows:
            w.writerow([ts, r["symbol"], r["price"], r["change_pct"]])


def run_once(client, args):
    rows = client.get_prices()
    if args.symbols:
        wanted = {s.upper() for s in args.symbols}
        rows = [r for r in rows if r["symbol"] in wanted]
    now = datetime.now(timezone.utc)
    if args.json:
        print(json.dumps({"fetched_at": now.isoformat(), "prices": rows}, indent=2))
    if args.csv:
        append_csv(args.csv, rows, now)  # save history even if a platform fails later
    if args.json:
        return

    # 1. New listings: announced on their own, whichever job spots them first.
    listing_error = None
    new = [] if args.symbols else (find_new_listings(rows) if args.send is not None else [])
    if new:
        ann = build_listing_posts(new, now)
        for n, (full, xtext) in enumerate(zip(ann["full"], ann["x"])):
            print(f"--- NEW LISTING: {new[n]['symbol']} ---\n{full}\n")
            try:
                send_everywhere({"full": full, "x": [xtext]}, args.send or ["telegram"])
                remember_symbols([new[n]])
            except ESXError as e:
                if getattr(e, "posted_somewhere", False):
                    remember_symbols([new[n]])  # announced somewhere; don't repeat it there
                listing_error = e  # still go on to the regular update
    elif args.send is not None and not args.symbols:
        remember_symbols(rows)

    # 2. The regular update.
    try:
        _regular_update(rows, now, args)
    finally:
        if listing_error:
            raise listing_error


def _regular_update(rows, now, args):
    if args.send is not None and args.skip_if_unchanged and prices_unchanged_since_last_post(rows):
        print("Prices unchanged since the last post - saved, not posted.")
        return
    if args.post or args.send is not None:
        idx = args.template if args.template is not None else _next_template_index()
        posts = {"full": build_post(rows, now, idx, args.session),
                 "x": build_x_posts(rows, now, idx, args.session)}
        print(posts["full"] + "\n")
        for i, part in enumerate(posts["x"], 1):
            print(f"--- X version, post {i}/{len(posts['x'])} ({x_length(part)}/{X_MAX_CHARS} chars) ---")
            print(part + "\n")
        if args.send is not None:
            try:
                send_everywhere(posts, args.send or ["telegram"])
            except ESXError as e:
                if getattr(e, "posted_somewhere", False):
                    remember_posted(rows)  # at least one platform got it; don't repeat it there
                raise
            remember_posted(rows)
    else:
        print_table(rows, now)


def main():
    p = argparse.ArgumentParser(description="Fetch ESX (esx.et) security prices.")
    p.add_argument("--symbols", nargs="+", help="only show these symbols, e.g. AWAB WGBX")
    p.add_argument("--csv", help="append each snapshot to this CSV file")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument("--post", action="store_true",
                   help="print a ready-to-post message (rotates through TEMPLATES)")
    p.add_argument("--send", nargs="*", metavar="PLATFORM",
                   help="post the message: --send (Telegram), --send linkedin, --send x, "
                        "--send telegram x ..., or --send all (every configured platform)")
    p.add_argument("--skip-if-unchanged", action="store_true",
                   help="with --send, don't post if prices are identical to the last post")
    p.add_argument("--session", choices=list(SESSIONS),
                   help="label the post as pre-opening, opening, hourly or closing")
    p.add_argument("--template", type=int, metavar="N",
                   help="with --post, use template N (0-based) instead of the rotation")
    p.add_argument("--watch", type=float, metavar="MINUTES", help="repeat every N minutes (min 5)")
    args = p.parse_args()

    client = ESXClient()
    if not args.watch:
        try:
            run_once(client, args)
        except (requests.RequestException, ESXError) as e:
            sys.exit(f"Error: {e}")
        return

    interval = max(args.watch, 5) * 60  # be gentle with the exchange's server
    while True:
        try:
            run_once(client, args)
        except (requests.RequestException, ESXError) as e:
            print(f"[{datetime.now():%H:%M}] fetch failed: {e}", file=sys.stderr)
        print()
        time.sleep(interval)


if __name__ == "__main__":
    main()
