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

# Optional display names. Symbols not listed here are shown as the bare ticker.
# Add or correct names as you like, e.g. "TELE": "Ethio Telecom".
NAMES = {
    "AWAB": "Awash Bank",
    "WGBX": "Wegagen Bank",
    "GDAB": "Gadaa Bank",
}

UP, DOWN, FLAT = "🟢", "🔴", "⚪"

# Each template gets: {date}, {body}, {ups}, {downs}, {flats}, {top_line}.
# The bot cycles through these in order, one per post, then starts over.
# Add, remove or edit freely; just keep {body} somewhere in each one.
TEMPLATES = [
    "📊 ESX Market Update | {date}\n\n{body}\n\n{top_line}\n🟢 {ups} up  🔴 {downs} down  ⚪ {flats} flat",

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
    pct_txt = "0.00%" if pct == 0 else f"{pct:+.2f}%"
    return f"{_status(pct)} {label}\n     ETB {r['price']:,.2f}  ({pct_txt})"


def _top_line(rows):
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


def _next_template_index():
    """Read the rotation position, advance it, and save it for next time."""
    try:
        with open(STATE_FILE) as f:
            idx = json.load(f).get("next", 0)
    except (OSError, ValueError):
        idx = 0
    idx %= len(TEMPLATES)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"next": (idx + 1) % len(TEMPLATES)}, f)
    except OSError:
        pass  # rotation just won't persist; the post still works
    return idx


def _post_datetime(dt):
    """e.g. 'Thu 24 Sep 2026 · 12:00 PM EAT'"""
    local = dt.astimezone(POST_TZ)
    hour = local.strftime("%I").lstrip("0")
    return f"{local:%a %d %b %Y} · {hour}:{local:%M %p} {POST_TZ_LABEL}"


def build_post(rows, fetched_at, template_index=None):
    """Return the post text. Uses the next template in rotation unless one is given."""
    if template_index is None:
        template_index = _next_template_index()
    # Gainers first, then flat, then decliners; blank line between securities.
    ordered = sorted(rows, key=lambda r: -r["change_pct"])
    body = "\n\n".join(format_security(r) for r in ordered)
    return TEMPLATES[template_index % len(TEMPLATES)].format(
        date=_post_datetime(fetched_at),
        body=body,
        ups=sum(r["change_pct"] > 0 for r in rows),
        downs=sum(r["change_pct"] < 0 for r in rows),
        flats=sum(r["change_pct"] == 0 for r in rows),
        top_line=_top_line(rows),
    ).strip()


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
    elif args.post or args.send:
        text = build_post(rows, now, args.template)
        print(text)
        if args.send:
            send_telegram(text)
            print("\n✅ Posted to Telegram.")
    else:
        print_table(rows, now)
    if args.csv:
        append_csv(args.csv, rows, now)


def main():
    p = argparse.ArgumentParser(description="Fetch ESX (esx.et) security prices.")
    p.add_argument("--symbols", nargs="+", help="only show these symbols, e.g. AWAB WGBX")
    p.add_argument("--csv", help="append each snapshot to this CSV file")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument("--post", action="store_true",
                   help="print a ready-to-post message (rotates through TEMPLATES)")
    p.add_argument("--send", action="store_true",
                   help="post the message to Telegram (needs TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
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
