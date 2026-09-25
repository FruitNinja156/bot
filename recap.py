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
import base64
import csv
import io
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
LINK = "t.me/BirrN_Official"
LOGO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.png")  # optional override
RATES = {"IBMM": "interbank rate"}  # shown as a rate, not a stock (same as esx_bot.py)

UP, DOWN, FLAT = "#1baf7a", "#e34948", "#b4b2a9"
INK, MUTED, GRID = "#1f1f1e", "#6b6a66", "#e6e5df"
NAVY, BRAND_LIGHT, TILE = "#204888", "#4878c0", "#eef3fa"  # BirrN brand colours (from the logo)
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
def load_logo():
    """assets/logo.png if you add one, otherwise the built-in BirrN logo."""
    try:
        if os.path.exists(LOGO_FILE):
            return plt.imread(LOGO_FILE)
        return plt.imread(io.BytesIO(base64.b64decode(LOGO_PNG_BASE64)), format="png")
    except Exception:  # a broken logo must never stop the recap
        return None


def new_canvas(title, subtitle):
    fig = plt.figure(figsize=(W, H), dpi=100, facecolor="white")
    fig.patches.append(plt.Rectangle((0, 0.991), 1, 0.009, transform=fig.transFigure, color=NAVY))
    fig.text(0.06, 0.955, title, fontsize=30, weight="bold", color=NAVY, va="top")
    fig.text(0.06, 0.912, subtitle, fontsize=15, color=MUTED, va="top")
    logo = load_logo()
    if logo is not None:
        h = 0.105
        w = h * (H / W) * logo.shape[1] / logo.shape[0]
        ax = fig.add_axes([0.94 - w, 0.978 - h, w, h])
        ax.imshow(logo)
        ax.axis("off")
    else:
        fig.text(0.94, 0.95, HANDLE, fontsize=13, color=MUTED, va="top", ha="right")
    return fig


def tiles(fig, items, top=0.858, height=0.075):
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
    ax.set_title(title, loc="left", fontsize=15, color=NAVY, pad=12, weight="bold")
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=MUTED, length=0, labelsize=11.5)


ROW = 0.056  # height of one row of facts


def facts_height(lines):
    return -(-len(lines) // 2) * ROW


def facts(fig, lines, bottom=0.085):
    """Two columns of label/value pairs, filled from the top, sitting just above the footer."""
    top = bottom + facts_height(lines)
    for i, (label, value) in enumerate(lines):
        x, y = (0.06 if i % 2 == 0 else 0.52), top - (i // 2) * ROW
        fig.text(x, y, label, fontsize=12, color=MUTED, va="top")
        fig.text(x, y - 0.021, value, fontsize=15, color=INK, va="top", weight="bold")


def footer(fig, extra=""):
    fig.add_artist(plt.Line2D([0.06, 0.94], [0.058, 0.058], transform=fig.transFigure, color=GRID, lw=1))
    fig.text(0.06, 0.036, f"BirrN  ·  {LINK}", fontsize=12.5, color=NAVY, weight="bold", va="center")
    fig.text(0.94, 0.036, f"Source: esx.et ticker{extra}", fontsize=11, color=MUTED, va="center", ha="right")
    fig.text(0.06, 0.016, "Informational only, not investment advice.", fontsize=10.5, color=MUTED, va="center")


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

    facts_top = 0.085 + facts_height(lines)
    facts(fig, lines)
    bar_top = 0.73
    if intraday:
        line_h = 0.16
        line_bottom = facts_top + 0.035
        hours = [t.hour + t.minute / 60 for t, _ in today]  # real time axis, so gaps look like gaps
        ticks = [(h, f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}") for h in range(int(min(hours)), int(max(hours)) + 1)]
        line_panel(fig, [0.1, line_bottom, 0.76, line_h], intraday, None,
                   "Intraday path (% vs first snapshot of the day)", xpos=hours, xticks=ticks)
        bar_bottom = line_bottom + line_h + 0.07
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
    facts_top = 0.085 + facts_height(lines)
    facts(fig, lines)
    line_h, line_bottom = 0.18, facts_top + 0.035
    idx = {s_: [100.0] + [100 * week_closes[d][s_][0] / base[s_] if s_ in week_closes[d] else None
                          for d in week_days] for s_ in order}  # "Start" = last close before the week
    line_panel(fig, [0.1, line_bottom, 0.76, line_h], idx, ["Start"] + [f"{d:%a}" for d in week_days],
               "Daily closes, indexed (start of week = 100)", lambda v, _: f"{v:.1f}")
    bar_bottom = line_bottom + line_h + 0.07
    bar_panel(fig, [0.14, bar_bottom, 0.8, 0.73 - bar_bottom], order, [wk[s_] for s_ in order],
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


# BirrN logo (PNG, transparent background), built in so no separate image file is needed.
# To use a different logo, add assets/logo.png to the repo instead of editing this.
LOGO_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAANsAAAEECAMAAACWQrE8AAAAflBMVEUjT4cAAAD7/PxObprm7/e2yN1yjK1HgMRIfsQqT3yc"
    "sszH1ebU4e6Inbi39vi1tbWysvx/f39///8A////+7FEWn1/f/8AAP/2trax/7HwsfD//wB//3//f/8Af38Af/8AAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB6wuloAAAAIHRSTlP+AAn+W/P9/v/+/tui/gUDAwICAQP9AgEEAwQBAgICAiEvticA"
    "ABkQSURBVHja1Z2HluwqrkAN2FXlVN0n3DDpzf//5TNCgBDC2e4ez1pzT+faJVBCSJU69Oj4xM+5L6Rf1eJ36vQXlL5v51Md"
    "wErhitD0xdPvUALOqXDVAYlxNr1IJ7z278iWrbr4CQeevdJf+N/Pj/gjf4WfPR/tABsDmHt5wmaCz/8nbNC1ZJuQq2NaRImv"
    "jnxU1/VAnp78e/oSfMs725Va2tnxr17MRt7c8Nfiy3tHrn56Gnw69+BH9iuBT5KV+PktK7U6LDZJf1th9Z5mfIjPOCLoJEsL"
    "+Kd+q19/CUpq98arDpm2oFEY2gTWjROToU+FT/LJibHrrACLClQVF+iFuoS+ue8/o8AsWAdcFX/a6X/sAb5Jfj3sQLbWb5Vb"
    "aUu8vbws1/gwElj5CXxBfqJaDU7PRXLTJdNs9YbdYE5e7fSsgGrhG+F7Ec/vPsGn0XfpEpXaNNhh3bhRXoL4pN3n1yhxh+6w"
    "b1FksBJBFHsfkB/gNYQuqmTv8FzKlmwHBTIbH4ckRhYpSq8JdN7K+P+7j83KzImsrc5gg8fTMcOwVWkes2+TzJruJJERONh7"
    "j2lp1omrutHlOuSXOJkZs1Ipbt55VnaC0ryazf4NUCBnyixTm2DzqOwW4gD2tWovml2N0z6rzpYZlV6VapVFRalP0CXqt64n"
    "3XihzJDNKs2x64fNIiuzlQJDcIrfQYW4P38hnFuajyi6XDY0wFrDVoiYcF38hJ02Xioytu/GBkRHbY/iTosAV80H1CmpJnr/"
    "cqQ2/oOKTkphLLBpPcdGA6pJ7z/MDVJrqU6Z6FB0hazMBrbcCUmF1t7ARuBE0aVO2Lr9Rtmy1A6ox4tZAhBzor2tEwOE9bY7"
    "vBtpGrLuQWjtHSqErskWFSaVnKL/EYPWGTbN5XapEjH5wwUorUuqOdf7XDrmeIJfPF4mNCNkwky2VidrkGWNym7mAhv5+cmo"
    "Xaf5rUiypzPS9xF9qTTJAGxjS5b2leuxDVupponA5iF5mJ3TKIqm5OUcUVVOtCeyB6t2mbafXnGuAuuMLWy61HqXPMUyW/p3"
    "+s5caMkm/b6SDeK6ppYCulk2TeMELjVzNZtKIutPVZCb8y+H7GxsYU1SQ3iW1NapVc+W6K+6qLtMl4lZ7WS7VmpOFITNa74Z"
    "Nio5pdbagET3ELNW3cCmyOHJX3NsVHKify+xaZWYDCe1yzxIkS2+kFm2KDlVzO5VBXtNtJWEdrJ3QtZktK5zbJP68XCquOcq"
    "pWaLJwp77TI2Tdj6WTYPp4oZ50rMGQdE6/jfEKwRPal8WmRWbtaDJtpSiZ5JJdQexKVZ93dkDxK5oeyW2JyjBu6XWrIB8orc"
    "5R63GJhsSDdTNr82l9hA2n2tV8cB3GTvQDPsPHvNmRxbk4Ft+ceSaHUDGzFs63WHTXTzB1KZa/XkBjaAW8mWa/88wl/8c50r"
    "HSHPcvYotQEuZlnDxryv1Wz1sMfTah99XeuZN2lxTVL7tuatLG45XRXPsPeF2e1jyIOlfiubXs9G9Qn7s5VYfvb+57KWKsst"
    "c202sMVqhJWvwG25z2W2uDCHnWHNJLfaHoeQ8teNbEF2q9isf9IP+iOPUQW2GLHtZ0v/zqY1SbIg69gmuC6cP6ahTFU4xt7r"
    "j6Rs+ga2lhiCNDlSiaUw+0M2z0YqXbawkdTGrK+c5r5IAoX6zBUNu6lDsjeHEHTJrjWpSbJ+LVtLDYFg39JgYdifZb2dDVNJ"
    "g1Awm8lNHUvYfQGby6XXOlaVCmye70hy/GvYYFXGs2+Zzf6rPpL7+So2G4S/WZUGZ7O77UgK+avYTJcHBFXmJtsjhvZ/ja3C"
    "rHvySzI2UCQH2T7vZ0sDAvdbMjbQ/7vZ2q9iy3J6AtvRA5svY6uSHQe/h7MNzaM6zPbxFWwVP+fK2I6es/kY5/b9lu04zW1A"
    "ffRM++vk1rLEEJGb+9XD0dMokNvHkThgO5vPg06CSxZllRZ89kePo47HOJRtLVpLbZzApjcGAIVKlxPZmtVyE3dczOFtNQDm"
    "RR/zQricTV/M1hYTeoku2aJJUjaLdwHb9uOgocS2xUsGtufTcT2n5xuwOW2imF8yU6xS+jXP1zOwWToj+Mqr2cxJbA+MUTWP"
    "cfptbNNDBff6BmyVXZQfXG6YSmg3rMknlVpZburGNUm0CT3rcJrkm7FtNbYtJJn/Ttl+qa3x9rdkg9pYXJTevuntPknG9jqd"
    "rd8RkpgxFOkFNrU1Kfld2cg5UhXUWb9t697CtuNs00ya8pP6k8C2LXLzNsACwr8K+01TG1Aot65ojeEhPTmxYWFGPKPankzg"
    "bC9JbmwnGxMkPRn+ybMxJrVvnG2P3MZQdFJRC5B7nxvktpHN+aALbHvkFn3KyleoDSey/UzZdGSLYntexEYi1Mr9uvfmbPJa"
    "NvUvkY3LravV+30CG6TPKRtc0v6ObHuSADH6Dmw2U94eYXvKbL8UZ8Ofm2dTR9iGlG04zEbk9oN12yG65Ol+An9EYvN1hrtz"
    "bsFfrqJ121bveYTteT3bO7Jti282sf04ma1doUwGKjd7nnie3NgV1Vpky/ebXsHWZk+JTUW2rfJf8ktIyzvC9oob7pXIjdzr"
    "WLUmi2SoTP6BbOpGttdzyS+ZYzNGbBll8hgO5WZT5/XmI46MzeRseoYtX5NJl5IC27ORnmd65dB5Jii39242XGMs7mblKlrw"
    "J93S5HJbuEcFN/uyp+eXAAObWwJDV13M1vI4IGdTIlsruRxp7bHMpiq4+nIyG+/KUWR7ldmUwBZNF63eyuyX6RrPpq9kY3FA"
    "CNWfC2yfMtuj77OWA1lhRWBzWukCNnrNoJPZnoEt5hTwdvJfcr4EI8/st7fMwNVeT+o9aZddbK9UlzC2tIGAz5e0spMftnSu"
    "TuGbfnkbcB9bCud0ib+TqTQ76BTs29izLjRCcAZs/z2T7SWw+bi7YzbApDYgsMUb2z+kMmd+09S9dJkNY5x9RyZ72F4JG7Hd"
    "lu2t0tvoXG60rRXqk/cvKa0KbJ9ebsMFbORcj65J9EcENp1cds3YoP2MtN4fcuR9FRurWg/2zfn/FiplqxI2aOrLfC4uNN/f"
    "RyyIfFA9eQIb9Sd5++Vou1/4Q1xu9L4psd2G6nVBaB9ysGDZ3vpEuUls5F5HGzbcM5dbtN1yHY8oNGATU1iJLukb0x5he4n+"
    "ZCwRiDlze5D8ytkesj9poo8s9dP5UciGm7Am34fZXq/0LD8pya9pmwmvKY2cexXjgFyJDK795sdHYcFZtj/1lWyxWc1IDjYQ"
    "SsgpfMpsWbcZ+xs79CvlQ4zptyHb8f3G2RjZw7XLdMJ7yjnzLpObuxKUd6+qbb/LhzPihXSBlduPq9mwax60W8TKfB91i2yK"
    "XXY1eYsguHBu3O2pUnbuEdbkx0Vs9YCNN+1b39e47V9luamk+zSiMaHBr/T3VOy1oRKb0yUns/ltX/e9J3Nv/bDElsubtz5y"
    "n8d+XR0kE+bZ9AVstos09pCGRn0q1Ga+5FwQ1ZPYjnTao5LQfE2hvZD8EJOqwefSVm57feWQ2n8lfgnIDFVI7CecsiU5vIre"
    "Eaux7W/oXhg/n1Suguotsqlflu1AHPAM5zIkhzdQ5dhHyzTDRs/yse8v90TkxmdtmQ10yc998dvLs73Q/UW2pncrClpuWZm9"
    "9Ra5oRIZM82/vteIY7O5oGOxKa+fnPRXF1QI8yZSNu5PzioR2IG53mhLhx011jxBnmvc7JdMMRicFXpjbCpaZ8F60FrhUTZ8"
    "Y7guGUpGbVPjM8wFKaydGbodbEThRbk5OCkiQbanyGZ7j/lG1O46+mfIiDglsqGrxth7Nr03h5ceXBhfJ2CVs9BKfokN2qLP"
    "CG1DiSCWmBxge9GqJxNVnmsjz9zDv6PtDsqV6BLTeScmeiIK1NCORqUTWzzr2HWOE+NSRsbedpWm7QtsI5LxxVzv6C5LznFs"
    "wuwYm0eDrSK2nFXM5/JKNgbWT9l93NMe5uHPOqDN9Z6zxRcev2ESCNM14kAKzX2uJ2eTNdDO7rIPcia879yUbTQxETUpiDq3"
    "b3Etv0iZlROaWvJEVheYJGztDraUjAvNjl2Ji8zrySdj8214g3JVeMt8ZzdP44rNI9v2GowEDci07yNCYu4x3m0qsblBFqRp"
    "OfG99vQ4s06N+hnyk2r7nT4zqxyjIY6HE57tka9Jv1Njz7PBxgK7JptAdaitakG/ZEcEZ+Jy9HEM3Svel/jD+q3/ttOmvJ6k"
    "YnNsQYko2ut1d5t7OFytCFu9nQ3f8XRqiAqhlptPEm+RZGz+3RFzIgeuT4IqqcidzHrXbXyqAahyjPGxj4EDW0jWErQ8vE7m"
    "tmwsWMK/SO7j7LiNGcPqj+RNh4QkzV38h8mNqCGIX/NwxiwGaQseV3KPanPPEq72FU21Ri0QD3ETn8uhEXN/Wl9xVxWkY7+g"
    "Td0vWt/0Pm407NEnjMvJ2WLUkBvF433ufWloWkO/lg2H1zTiRsteWezhQNiIEjm7Q7UP3pI7YqssXEvG8tRZi9FG8NklNlyP"
    "o6xEDrJ1yBbl9i+8s9Ius8k+8fTCxB0LbH+rYN/8qUcmtPqcCSemGbK7D6usNzp+uX8FMpPQDJeboPn/EU3iYTS/v5M7Kysv"
    "rQQynZ9DFa2NIjGO14/Zkdo5XZxDN4yEbfH2g4tDBFsdB9uV2f5JLejKxOouLdkN7A40/Hu5v2qWxl5W257tk7BlQjtFiYQY"
    "QOd3oNWChQMPoqAcy6JmaxJkP7LBbidO3Il9/9L5OOF2n5hjF6fvLK2llukS0/5Bz+VVcjpzBlvS9q9KO0UU2Lxy/K1/stmY"
    "43wPZM4GRi2ZpDi405kz0Fpapoc58zBwu6BN5IRBnTu1M+cOwPZIw4bPvYmsMpuN8t85m3Q07jMZDe6Q2DgvKMeFVDZla0b+"
    "FrkJm9VJXcXxXp+/6eTvv/mGCp3AZvU1HjXR8z/rE7eLva8JW93zqazYnPqkxu++w3koC0vYct8k8xx/+o32QLJ29ZrUdXKy"
    "Ez2R05raJ1oS76zw7lz2j7WlsJoeOy+/KoyChanxOCapOu9JNYnSFeuqhmwtUY6szMd7jv4NWMmm6G798EUW57KNfTJfrEpb"
    "a3pt0hZq3zD43Phe5mK7YNZay7rOaC634DBnal95z3HbSwp5LnX5lCTWLShj89rEPJm2/qRv9obND2wf6wZlnBPd8B5W5KII"
    "CE4oWKy959huZ8vIrpjcAtHNr4wtSVTZHZcPDnIKuz2gu9SlU7vibouiqmLDOPw/KziqTYlB22GJwm9SiSdy8niTNu420qYo"
    "7a/snFfrcXRE929WjuU1iaczp0st9sYmRfhJfy57POxmIphYJlYvevur2epT3cfE24q5hGjTOJufZDSFxqdYIsp21USalkw5"
    "KrH5/9r0uZOx9R6853iU7bopm3BXKb9OkvT6oy3/rJBr780eGKwe2OrrZna1Lpn85v1u0pmE8Kk3rsopagND1B6ZGY9s9UVG"
    "TfS2JDbS0dwuRHtx2hx+R4HNK5GLnuhp0Cs9bG5fKAXpR2POGas+saHQLps+L4wPKMjNddg5bdtPy6W/dmIXXZHzbKp4rWzv"
    "mzo95sqp2NT3TSb9MLYQg7u25me8oJPW9pYViSMEKj5uPRm0dMsw68NZ8npx1hZzoo8M7bjtaWk0tjD/jcLVl081PcePLIyj"
    "ktjoHa3um69KNi1zA9v/wKqMLv2K2XYqzdd13xuOnCSumdunjg9cvFdqau28RTrB9vKx3ccNZ9fXa9joyHnN9AlPTXwXtixh"
    "VZ5xzccu4tFfd6Bu7EINmRxtL8lN8/l24dTsWy5LhxaGEM/NbtUZXKyp+X5wUWrJZpLZuExp3uEbmjkT055K6a1s4dPvbyg5"
    "KHRJWqSohVnJ0je4cqvvBkeu/qkZwVV8GbLxsFCve/4R5zEN2bFAW2tRm8hzMrMpvNcl4PZqSJ2Mxlxak5LCdHC/4YLdd4Br"
    "3Q0QZq9Xsgmja688D9zjHqfXJFT5qeZWZHrMf15F2TG0QbyCpvSy3PI1quj5+5dvtV6+YKfX6BJhlLdKzk6/VGhFtL1seErw"
    "1QFdYT2SFoEr2YLRSLuw4mncF0QD8p3xWZVSqfkn6Uv7qb/MGKQl9/Pqbw+bilfJzd1CK6FtsAFzbKSE5tZd10YlovLkuD7G"
    "lo1AtSfFxlTV+adO2S+EeySsCFytfKrVaEqowT6XTa5azG/IXMsGXsrpdDIZ3kh662L+eO+aVLpk8i7XKeHWN+/VeRqbZM4V"
    "vV9kLtT7/PLPDWz6hGKolXpfaqlxLpuWS45dEdsKg7Cnxk26k3A2m2ZNQIU7fSQmb0taYpNKsXksemv8IjbNpxQlz29/6dkv"
    "zfaEtQj34UFmKnr4c4n/I35JyEvIooMOA+M5G4/2YntP/4Nlk+myE9nIfivIb4KzG+9xqCbBdfXqfC82waE6d01mCzKtb1Yh"
    "tqtRr6D42j16cQKzIqujqdbCC1KXsWkqw1Q72+aFE94YxbdShVh5TXtsAgOytKvtzWylfQfSG+zydHRzBXzxKyCwZtKLsmJU"
    "u59VNiD3MGcYaxSfXZ8PYUZfMrzvYdchCqzYRCnNBp/JRlVKmmtgR/8JHwrQLdGHyR9Ygw2KKxMYXTPCp85nI3Pic40lmvXB"
    "ihAI4RnHEf9lsex1OL8OhZ/nZVhyavgUfzLZd0kVUZEtiNA9QDo4MdWprN6aVxEoMs/5uv3GDB1XVdwuqKJT60D5SUr+7qS7"
    "QM9m9E9ho5EhdZwVn/nMdMwPaKn+Lkn1jbuY+QZZIa6+hY1VtLG8LgtG2HbRShVVrOwoUE25eYFuWZML0Rx7vbyaFj/4S8fS"
    "gjzlLWGnIryCrRD7zOEJqkEsG0hT13rmfbtIbuvi8bzyOb54lbLpVD8kif1y1l9dYQOKW3BG/6eBHrOMmh87sPX3dWyipMqb"
    "r2RBlC4tbXk53LImmXOk1rPNJgZLb1lm/i5kY46f9CIV8Usyj03NyzxXS3vgjsttRkouU9T0oRMAf6HzW/UjjnpTe1blUV0i"
    "uubxC7aN5AMKlGJhrVixz98hZg02xwCH7Vty0g/uMPWCsVkb9tdUyYgsga0OQYG7v1vTWPW+NZl7XrULZnp8fKBZN48/7K3C"
    "btBFXyNmccMPuqJU+EVcmdxj35I/aG+BxWcKz4aBsjXDkv2D3rBjPNKu4Rd2fqi03mMEqqO77Tc09MbRHDGqdscvZE3mqiPR"
    "tzg9JbwLA3ZtH+CqvbqfzWuH32EccevvWxgH1zdhls3807iOKOFtGOxc6tbdbM4jkVt0CWqTeL3FJX1aLHCHs/FQ8BK0TPiQ"
    "sI2Ozd+vGeCKWs52g+1W1HDHC2X+kmrrN1kNCSF4uaBt7MCeYehr/IpNR4J+1L5A0/3Yj4RNCycT99juN7L5XqL24MNVcA7e"
    "vllZuFFLk5KZlum08Ab8ENJ9k3FvwghU+83/J7BtD1CP+lzofjg2OAwc+iawoS6xhdOu85jpIC/bDa5PlH0zRigbjGPUnDrB"
    "ho5hTe7JDJ3J1rrb8bYunbMNNXb6s0v2D2QLadixIbPRXVkrZduRmjwnxglTBy3bJIC6xpdtDTa0gG8d24g9sO0nuj7e1YVL"
    "bHTuO6xKYEt0ibot7uZHVMBmi1ygpVCF90CxtZJjCywot/jh2Cds9i2ibHrzKccJfkkwx2gD4JTJne677g04xpPIDSQ3Np6t"
    "rbD9n1vU0FUULiM2lG1zxH2cLQbN/rqVIZ1qQdE3GZs9PZw+9HIz7swN7Zt5WhNu4V2vdst2Qw6vcMhXuEqGxeCRTfv95g58"
    "a9xvMODD2vAedT6MBjMGi45ytjvsm4q5K5gyGiYG+z684PZSNvcdDywZcXID16wOg6pbZyJgcRtv3z4Ym76HjbSq6aJf4l8W"
    "YxtRqTtHC4wgdsl4R7bGjR2o8GjSkODoPjbRdtumh03jpgZaG1BnejK8WMqmKFsfbm21X86mqV/SOP+4cwphMnbgbpgFNrIm"
    "7Td6OMaWHCpczqaS9kmOLfjwYOAimyZsyrO1udwGvL8LTRJFtttsAGMDv6RGP9dKi7ARJ+qdyu3fNsYJbG6TQhkAtW93svmc"
    "Pm2aYaytanA2FpcbdRDzNUnYnEMKq9L7JWni666zDk1jU+eXuKaiuN/AFqOv7Nne2PvFeZJWyStvu2FqT+heStiU2p40OZSf"
    "DP/w7mCotoBYzunJxFeO+y2yQcLF+VyWzc/XaD3bT80PRW5h876JD79oQYy3bxIb9OA2nk25nELr16QPk4ie/BI2jWy+QDTk"
    "uSAXlMemfn8NLjYNU4/68UHSXLUfaUs7wRWS2Jftt9DQq4klMjaJ4Gc42df4AL8ZhjDFbrNwm85l08MvII1W7JcfBD1xYG9k"
    "8zWG5Ol7n9GCBBCOqm2aWFmNSaJ+CK1k/ZdDoTf9dprIvisO4EUy8N+ZMhr2cXjd//Qff5J5W3XNj/KVvisOIPn8X8mXPlXm"
    "SehfP1iesSpUjGhxZagdFULVQcvGSmE+C4fd4TV+FKoIQ+VTivSp2fH4TWf5SgklnFLJWaH0UgkHA4qnWY8UUVbnoqmyVISD"
    "a1o1Ml/Pvgvu/wE+Z5Y0mEcD7AAAAABJRU5ErkJggg=="
)

if __name__ == "__main__":
    main()
