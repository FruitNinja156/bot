#!/usr/bin/env python3
"""
recap.py - build daily and weekly ESX recap images from data/prices.csv.

Nothing is posted. Images + suggested captions are saved for you to post manually:
  reports/daily/2026-09-24.png   + .txt caption      reports/latest-daily.png  (always the newest)
  reports/weekly/2026-W39.png    + .txt caption      reports/latest-weekly.png

  python recap.py --daily                      # today's recap (Ethiopia date)
  python recap.py --weekly                     # this week's recap (Mon-Fri)
  python recap.py --daily --date 2026-09-24    # a specific day / the week containing it

This file only READS the price history. It never changes esx_bot.py or the regular posts.
Analytics are descriptive (what happened), never buy/sell advice.
"""
import argparse
import csv
import os
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

EAT = ZoneInfo("Africa/Addis_Ababa")
HANDLE = "@BirrN_Official"
RATES = {"IBMM": "interbank rate"}  # shown as a rate, not a stock (same as esx_bot.py)

UP, DOWN, FLAT = "#1baf7a", "#e34948", "#b4b2a9"
INK, MUTED, TILE, GRID = "#1f1f1e", "#6b6a66", "#f4f3ef", "#e6e5df"
SERIES = ["#2a78d6", "#eb6834", "#6250d6", "#eda100", "#e87ba4", "#5f5e5a", "#0c447c", "#993c1d"]  # no green/red
W, H = 10.8, 13.5  # inches at 100 dpi = 1080 x 1350 px (fits Telegram, LinkedIn, X)


# ---- data ---------------------------------------------------------------------------------------
def load_history(path):
    """{snapshot_time (EAT): {symbol: (price, change_pct)}} in time order."""
    snaps = defaultdict(dict)
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            t = datetime.fromisoformat(row["fetched_at_utc"]).astimezone(EAT)
            snaps[t][row["symbol"]] = (float(row["price_etb"]), float(row["change_pct"]))
    return dict(sorted(snaps.items()))


def by_day(snaps):
    days = defaultdict(list)
    for t, data in snaps.items():
        days[t.date()].append((t, data))
    return dict(sorted(days.items()))


def closes(days):
    """Last snapshot of each day: {date: {symbol: (price, change_pct)}}."""
    return {d: s[-1][1] for d, s in days.items()}


def is_stock(sym):
    return sym not in RATES


def pct(a, b):
    return 0.0 if a == 0 else (b / a - 1) * 100


def fmt_pct(v):
    return "0.00%" if abs(v) < 0.005 else f"{v:+.2f}%"


def color(v):
    return UP if v > 0.004 else DOWN if v < -0.004 else FLAT


def streaks(day_closes, upto):
    """{symbol: (+n or -n)} = consecutive up/down days ending on `upto`, by ESX's daily change."""
    out = {}
    ordered = [d for d in day_closes if d <= upto]
    syms = day_closes[upto].keys() if upto in day_closes else []
    for sym in filter(is_stock, syms):
        n = 0
        for d in reversed(ordered):
            if sym not in day_closes[d]:
                break
            c = day_closes[d][sym][1]
            if c > 0 and n >= 0:
                n += 1
            elif c < 0 and n <= 0:
                n -= 1
            else:
                break
        out[sym] = n
    return out


# ---- drawing helpers ----------------------------------------------------------------------------
def new_canvas(title, subtitle):
    fig = plt.figure(figsize=(W, H), dpi=100, facecolor="white")
    fig.text(0.06, 0.955, title, fontsize=30, weight="bold", color=INK, va="top")
    fig.text(0.06, 0.915, subtitle, fontsize=15, color=MUTED, va="top")
    fig.text(0.94, 0.95, HANDLE, fontsize=13, color=MUTED, va="top", ha="right")
    return fig


def tiles(fig, items, top=0.875, height=0.075):
    """Row of KPI tiles: [(label, value, value_color), ...]."""
    n, x0, gap = len(items), 0.06, 0.015
    w = (0.88 - gap * (n - 1)) / n
    for i, (label, value, col) in enumerate(items):
        x = x0 + i * (w + gap)
        fig.patches.append(FancyBboxPatch((x, top - height), w, height, boxstyle="round,pad=0,rounding_size=0.012",
                                          transform=fig.transFigure, facecolor=TILE, edgecolor="none"))
        fig.text(x + 0.018, top - 0.018, label, fontsize=12.5, color=MUTED, va="top")
        fig.text(x + 0.018, top - height + 0.014, value, fontsize=24, weight="bold", color=col, va="bottom")


def bar_panel(fig, rect, labels, values, title):
    ax = fig.add_axes(rect)
    ys = range(len(labels))[::-1]
    lim = max([abs(v) for v in values] + [1.0]) * 1.35
    ax.barh(list(ys), [v if abs(v) > 0.004 else (0.03 * lim) for v in values], height=0.62,
            color=[color(v) for v in values], zorder=3)
    for y, v in zip(ys, values):
        ax.text(v + (0.04 * lim if v >= 0 else -0.04 * lim), y, fmt_pct(v), va="center",
                ha="left" if v >= 0 else "right", fontsize=12.5, color=INK, zorder=4)
    ax.set_yticks(list(ys))
    ax.set_yticklabels(labels, fontsize=13.5, color=INK)
    ax.set_xlim(-lim, lim)
    ax.axvline(0, color="#c3c2b7", lw=1, zorder=2)
    _style(ax, title)
    ax.set_xticks([])
    ax.grid(False)
    return ax


def line_panel(fig, rect, series, xlabels, title, ylabel_fmt=None, xpos=None, xticks=None):
    """series = {symbol: [y values aligned with xlabels/xpos, None for missing]}.
    xpos: real x positions (e.g. hour of day) instead of evenly spaced categories."""
    ax = fig.add_axes(rect)
    ends = []
    positions = xpos if xpos is not None else list(range(len(xlabels)))
    for i, (sym, ys) in enumerate(series.items()):
        pts = [(x, y) for x, y in zip(positions, ys) if y is not None]
        if not pts:
            continue
        xs, vs = zip(*pts)
        c = SERIES[i % len(SERIES)]
        ax.plot(xs, vs, color=c, lw=2.2, solid_capstyle="round", zorder=3)
        ax.scatter([xs[-1]], [vs[-1]], color=c, s=36, zorder=4, edgecolor="white", linewidth=1.5)
        ends.append([vs[-1], vs[-1], xs[-1], sym, c])
    allv = [v for ys in series.values() for v in ys if v is not None]
    span = (max(allv) - min(allv)) or 1
    if ylabel_fmt is None:  # % change: fewer decimals when the moves are big
        dec = 2 if span < 1 else 1 if span < 5 else 0
        ylabel_fmt = lambda v, _: f"{v:+.{dec}f}%" if abs(v) > 1e-9 else "0%"  # noqa: E731
    xr = (max(positions) - min(positions)) or 1
    gap = span * 0.11  # minimum vertical distance between end labels
    ends.sort(key=lambda e: e[0])
    for j in range(1, len(ends)):
        ends[j][1] = max(ends[j][1], ends[j - 1][1] + gap)
    shift = (sum(e[0] for e in ends) - sum(e[1] for e in ends)) / len(ends) if ends else 0
    for real, lab, x, sym, c in ends:
        ax.annotate(sym, (x, real), xytext=(x + 0.03 * xr, lab + shift), textcoords="data", va="center",
                    fontsize=11.5, color=c, weight="bold")
    ax.margins(y=0.12)
    if xticks:
        ax.set_xticks([t for t, _ in xticks])
        ax.set_xticklabels([l for _, l in xticks], fontsize=11.5, color=MUTED)
    else:
        ax.set_xticks(positions)
        ax.set_xticklabels(xlabels, fontsize=11.5, color=MUTED)
    ax.set_xlim(min(positions) - 0.04 * xr, max(positions) + 0.16 * xr)
    ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(5))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(ylabel_fmt))
    _style(ax, title)
    ax.grid(axis="y", color=GRID, lw=1, zorder=0)
    ax.grid(axis="x", visible=False)
    return ax


def _style(ax, title):
    ax.set_title(title, loc="left", fontsize=15, color=INK, pad=12, weight="bold")
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=MUTED, length=0, labelsize=11.5)


ROW = 0.056  # height of one row of facts


def facts_height(lines):
    return -(-len(lines) // 2) * ROW


def facts(fig, lines, bottom=0.075):
    """Two columns of label/value pairs, filled from the top, sitting just above the footer."""
    top = bottom + facts_height(lines)
    for i, (label, value) in enumerate(lines):
        x, y = (0.06 if i % 2 == 0 else 0.52), top - (i // 2) * ROW
        fig.text(x, y, label, fontsize=12, color=MUTED, va="top")
        fig.text(x, y - 0.021, value, fontsize=15, color=INK, va="top", weight="bold")


def footer(fig, extra=""):
    fig.text(0.06, 0.03, f"Source: esx.et ticker{extra}. Informational only, not investment advice.",
             fontsize=11, color=MUTED)


def save(fig, png_path, latest_path, caption, caption_path):
    os.makedirs(os.path.dirname(png_path), exist_ok=True)
    fig.savefig(png_path, dpi=100, facecolor="white")
    plt.close(fig)
    shutil.copyfile(png_path, latest_path)
    with open(caption_path, "w") as f:
        f.write(caption + "\n")
    print(f"Saved {png_path}\n      {caption_path}\n      {latest_path}")


# ---- daily ----------------------------------------------------------------------------------------
def daily(snaps, day, out):
    days = by_day(snaps)
    if day not in days:
        raise SystemExit(f"No prices saved for {day} yet - nothing to recap.")
    day_closes = closes(days)
    today = days[day]
    close_t, close = today[-1]
    stocks = sorted([s for s in close if is_stock(s)], key=lambda s: -close[s][1])
    chg = {s: close[s][1] for s in stocks}
    ups, downs = sum(v > 0 for v in chg.values()), sum(v < 0 for v in chg.values())
    avg = sum(chg.values()) / len(chg) if chg else 0

    fig = new_canvas("ESX daily recap", f"{day:%a %d %b %Y} · as of {close_t:%-I:%M %p} EAT")
    tiles(fig, [("Advancers", str(ups), UP), ("Decliners", str(downs), DOWN),
                ("Unchanged", str(len(chg) - ups - downs), INK), ("Average move", fmt_pct(avg), color(avg))])

    # Intraday path, only if prices actually moved between today's snapshots.
    intraday = {}
    for s_ in stocks:
        ys = [data[s_][0] for _, data in today if s_ in data]
        if len(ys) > 1 and max(ys) != min(ys):
            intraday[s_] = [pct(ys[0], data[s_][0]) if s_ in data else None for _, data in today]

    lines = []
    if stocks:
        lines += [("Top gainer", f"{stocks[0]} {fmt_pct(chg[stocks[0]])}"),
                  ("Top loser", f"{stocks[-1]} {fmt_pct(chg[stocks[-1]])}")]
    st = streaks(day_closes, day)
    best = max(st.items(), key=lambda kv: abs(kv[1]), default=(None, 0))
    if abs(best[1]) >= 2:
        lines.append(("Longest streak", f"{best[0]} {'up' if best[1] > 0 else 'down'} {abs(best[1])} days in a row"))
    prior = [d for d in day_closes if d < day]
    if len(prior) >= 5:
        base = day_closes[prior[-5]]
        five = {s_: pct(base[s_][0], close[s_][0]) for s_ in stocks if s_ in base}
        if five:
            b_, w_ = max(five, key=five.get), min(five, key=five.get)
            lines += [("5-day best", f"{b_} {fmt_pct(five[b_])}"), ("5-day worst", f"{w_} {fmt_pct(five[w_])}")]
    for sym, label in RATES.items():
        if sym in close:
            lines.append((f"{sym} ({label})", f"{close[sym][0]:.2f}%"))

    facts_top = 0.075 + facts_height(lines)
    facts(fig, lines)
    bar_top = 0.745
    if intraday:
        line_h = 0.16
        line_bottom = facts_top + 0.035
        hours = [t.hour + t.minute / 60 for t, _ in today]  # real time axis, so gaps look like gaps
        ticks = [(h, f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}") for h in range(int(min(hours)), int(max(hours)) + 1)]
        line_panel(fig, [0.1, line_bottom, 0.76, line_h], intraday, None,
                   "Intraday path (% vs first snapshot of the day)", xpos=hours, xticks=ticks)
        bar_bottom = line_bottom + line_h + 0.075
    else:
        fig.text(0.06, facts_top + 0.035, "No intraday price changes were recorded today.", fontsize=12, color=MUTED)
        bar_bottom = facts_top + 0.085
    bar_panel(fig, [0.14, bar_bottom, 0.8, bar_top - bar_bottom], stocks, [chg[s_] for s_ in stocks],
              "Daily change by ticker")
    footer(fig, f" · {len(today)} snapshot{'s' if len(today) != 1 else ''} today")

    caption = "\n".join([
        f"📊 ESX Daily Recap · {day:%a %d %b %Y}", "",
        f"🟢 {ups} up · 🔴 {downs} down · ⚪ {len(chg) - ups - downs} flat",
        *([f"🚀 Top gainer: {stocks[0]} {fmt_pct(chg[stocks[0]])}",
           f"⚠️ Top loser: {stocks[-1]} {fmt_pct(chg[stocks[-1]])}"] if stocks else []), "",
        "#ESX #Ethiopia #EthiopianSecuritiesExchange #BirrN"])
    save(fig, f"{out}/daily/{day:%Y-%m-%d}.png", f"{out}/latest-daily.png", caption, f"{out}/daily/{day:%Y-%m-%d}.txt")


# ---- weekly ---------------------------------------------------------------------------------------
def weekly(snaps, day, out):
    days = by_day(snaps)
    monday = day - timedelta(days=day.weekday())
    week_days = [d for d in days if monday <= d <= monday + timedelta(days=4)]
    if not week_days:
        raise SystemExit(f"No prices saved for the week of {monday} yet - nothing to recap.")
    week_closes = closes({d: days[d] for d in week_days})
    first = days[week_days[0]][0][1]  # earliest snapshot of the week (Monday 9:00 = last Friday's close)
    last = week_closes[week_days[-1]]
    syms = [s for s in last if is_stock(s)]
    base = {s: next(data[s][0] for d in week_days for _, data in days[d] if s in data) for s in syms}
    wk = {s: pct(base[s], last[s][0]) for s in syms}
    order = sorted(syms, key=lambda s: -wk[s])
    ups, downs = sum(v > 0.004 for v in wk.values()), sum(v < -0.004 for v in wk.values())
    iso = monday.isocalendar()

    fig = new_canvas("ESX weekly recap", f"Week {iso.week} · {monday:%d %b} – {week_days[-1]:%d %b %Y}")
    tiles(fig, [("Up for the week", str(ups), UP), ("Down", str(downs), DOWN),
                ("Unchanged", str(len(syms) - ups - downs), INK), ("Trading days", str(len(week_days)), INK)])
    lines = []
    if order:
        lines += [("Best of the week", f"{order[0]} {fmt_pct(wk[order[0]])}"),
                  ("Worst of the week", f"{order[-1]} {fmt_pct(wk[order[-1]])}")]
    for sym, label in RATES.items():
        if sym in last and sym in first:
            lines.append((f"{sym} ({label})", f"{first[sym][0]:.2f}% → {last[sym][0]:.2f}%"))
    facts_top = 0.075 + facts_height(lines)
    facts(fig, lines)
    line_h, line_bottom = 0.18, facts_top + 0.035
    idx = {s_: [100.0] + [100 * week_closes[d][s_][0] / base[s_] if s_ in week_closes[d] else None
                          for d in week_days] for s_ in order}  # "Start" = last close before the week
    line_panel(fig, [0.1, line_bottom, 0.76, line_h], idx, ["Start"] + [f"{d:%a}" for d in week_days],
               "Daily closes, indexed (start of week = 100)", lambda v, _: f"{v:.1f}")
    bar_bottom = line_bottom + line_h + 0.075
    bar_panel(fig, [0.14, bar_bottom, 0.8, 0.745 - bar_bottom], order, [wk[s_] for s_ in order],
              "Change for the week by ticker")
    footer(fig)

    caption = "\n".join([
        f"📅 ESX Weekly Recap · Week {iso.week} ({monday:%d %b} – {week_days[-1]:%d %b %Y})", "",
        f"🟢 {ups} up · 🔴 {downs} down · ⚪ {len(syms) - ups - downs} flat for the week",
        *([f"🏆 Best: {order[0]} {fmt_pct(wk[order[0]])}", f"📉 Worst: {order[-1]} {fmt_pct(wk[order[-1]])}"]
          if order else []), "",
        "#ESX #Ethiopia #EthiopianSecuritiesExchange #BirrN"])
    name = f"{iso.year}-W{iso.week:02d}"
    save(fig, f"{out}/weekly/{name}.png", f"{out}/latest-weekly.png", caption, f"{out}/weekly/{name}.txt")


def main():
    p = argparse.ArgumentParser(description="Build ESX recap images (nothing is posted).")
    p.add_argument("--daily", action="store_true", help="build the daily recap")
    p.add_argument("--weekly", action="store_true", help="build the weekly (Mon-Fri) recap")
    p.add_argument("--date", help="YYYY-MM-DD (default: today in Ethiopia)")
    p.add_argument("--csv", default="data/prices.csv", help="price history file")
    p.add_argument("--out", default="reports", help="folder to save images and captions in")
    a = p.parse_args()
    if not (a.daily or a.weekly):
        p.error("choose --daily and/or --weekly")
    day = date.fromisoformat(a.date) if a.date else datetime.now(timezone.utc).astimezone(EAT).date()
    snaps = load_history(a.csv)
    if a.daily:
        daily(snaps, day, a.out)
    if a.weekly:
        weekly(snaps, day, a.out)


if __name__ == "__main__":
    main()
