"""Daily alert: a short summary of one journaled session, sent to Telegram, by email and as a comment on a
"Daily alerts" GitHub issue (`aistock alert`; run at the end of the Daily scan workflow, and by hand for any
date through the Send alert workflow).

Telegram gets the summary as an image card (`card_html`, rendered by headless Chrome, sent with `caption_html`
holding the links), falling back to `telegram_html` text when no image can be made.

`summary` reads the session's journal day file only (no LLM, decision 0001): the BUY signals with each
stock's company name, sector, combined score and strategies, which of them are new since the previous session, and
which BUYs were dropped. Paper accounts are left to the dashboard. Each channel gets its own rendering of the
same summary: `telegram_html` (a block per new BUY, the top BUYs aligned), `markdown` (tables, for the issue)
and `plain` (email and the terminal).
"""
import html
import json
import os
import smtplib
import subprocess
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ISSUE_TITLE = "Daily alerts"
MAX_NEW = 8       # new BUYs listed one per line
TOP = 5           # strongest BUYs by combined score
MAX_DROPPED = 8   # dropped symbols on their one line


@dataclass
class Stock:
    symbol: str
    name: str      # company name without the legal suffix
    sector: str
    combined: float
    strategies: list[str]


@dataclass
class Summary:
    date: str
    prev: str | None
    buys: list[Stock]  # best combined score first
    new: list[Stock]   # BUYs that weren't BUYs in the previous session, best first
    dropped: list[str]
    actions: dict[str, int]              # stocks per verdict (BUY / WATCH / AVOID)
    sectors: list[tuple[str, int]]       # sectors with the most BUYs, most first
    prev_share: float | None = None      # share of scanned stocks saying BUY in the previous session
    prev_sectors: dict[str, int] = field(default_factory=dict)  # BUYs per sector in the previous session


def _names(symbols: list[str], limit: int) -> str:
    more = len(symbols) - limit
    return ", ".join(symbols[:limit]) + (f" +{more} more" if more > 0 else "")


def _short(name: str) -> str:
    """Company name without the legal suffix: "Mphasis Limited" -> "Mphasis"."""
    for suffix in (" Limited", " Ltd.", " Ltd"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def sessions(root: Path = ROOT) -> list[str]:
    return [p.stem for p in sorted((root / "journal" / "days").glob("*.csv"))]


def summary(root: Path = ROOT, date: str | None = None) -> Summary | None:
    """What the alert says about session `date` (default: the latest journaled one); None if there is no
    session. Raises ValueError for a date with no journaled session."""
    days = sessions(root)
    if not days:
        return None
    date = date or days[-1]
    if date not in days:
        before = [d for d in days if d < date]
        raise ValueError(f"no session on {date}" + (f"; the latest before it is {before[-1]}" if before else ""))
    i = days.index(date)
    today = pd.read_csv(root / "journal" / "days" / f"{date}.csv")
    rows = today[today.action == "BUY"].sort_values("combined", ascending=False)
    get = lambda r, col: r.get(col) if isinstance(r.get(col), str) else ""
    buys = [Stock(r["symbol"], _short(get(r, "name")) or r["symbol"], get(r, "sector"), float(r["combined"]),
                  [b for b in get(r, "buys").split(";") if b]) for _, r in rows.iterrows()]
    by_sector = lambda df: {k: int(v) for k, v in df.sector.value_counts().items()} if "sector" in df else {}
    prev, new, dropped, prev_share, prev_sectors = None, [], [], None, {}
    if i > 0:
        prev = days[i - 1]
        before = pd.read_csv(root / "journal" / "days" / f"{prev}.csv")
        was = set(before.symbol[before.action == "BUY"])
        new = [b for b in buys if b.symbol not in was]
        dropped = sorted(was - {b.symbol for b in buys})
        prev_share = len(was) / len(before) if len(before) else None
        prev_sectors = by_sector(before[before.action == "BUY"])
    actions = today.action.value_counts().to_dict()
    sectors = list(by_sector(rows).items())
    return Summary(date, prev, buys, new, dropped, actions, sectors, prev_share, prev_sectors)


# ---------- rendering: one summary, three formats ----------

def _day(d: str) -> str:
    return datetime.strptime(d, "%Y-%m-%d").strftime("%a %d %b %Y")


def _score(x: float) -> str:
    return f"{x:+.2f}".replace("-", "−")


def _verdicts(s: Summary) -> str:
    total = sum(s.actions.values()) or 1
    share = s.actions.get('BUY', 0) / total
    return (f"{s.actions.get('BUY', 0)} BUY · {s.actions.get('WATCH', 0)} WATCH · {s.actions.get('AVOID', 0)} AVOID "
            f"({share:.0%} of {total} stocks say BUY"
            + (f", {_change(share, s.prev_share, pct=True)[1:-1]} on the previous session" if s.prev_share is not None else "")
            + ")")


def _hot(s: Summary) -> str:
    return " · ".join(f"{k} {n}" for k, n in s.sectors[:3])


def _strategies(b: Stock) -> str:
    """Every strategy that says BUY, by name (which strategies fired matters more than a short line)."""
    return ", ".join(b.strategies)


def _more(items: list, limit: int) -> int:
    return max(0, len(items) - limit)


def plain(s: Summary, url: str | None = None) -> str:
    """Plain text: email and the terminal."""
    lines = [f"AI Stock · {_day(s.date)}", _verdicts(s)] + ([f"Hot sectors: {_hot(s)}"] if s.sectors else [])
    if s.new:
        lines += ["", f"New BUYs ({len(s.new)})"]
        for strategies, group in _groups(s.new[:MAX_NEW]):
            lines.append(f"  [{', '.join(strategies)}] ({len(group)})")
            lines += [f"    {b.symbol} · {b.name} · {b.sector} · score {_score(b.combined)}" for b in group]
        if _more(s.new, MAX_NEW):
            lines.append(f"  +{_more(s.new, MAX_NEW)} more")
    if s.buys:
        width = max(len(b.symbol) for b in s.buys[:TOP])
        lines += ["", f"Top {min(TOP, len(s.buys))} by score"]
        lines += [f"  {b.symbol:<{width}}  {_score(b.combined)}  {b.sector}" for b in s.buys[:TOP]]
    if s.dropped:
        lines += ["", f"Dropped: {_names(s.dropped, MAX_DROPPED)}"]
    if url:
        lines += ["", f"Dashboard: {url}"]
    return "\n".join(lines)


SECTOR_ICON = {"Technology": "💻", "Financial Services": "🏦", "Healthcare": "💊", "Industrials": "🏭",
               "Consumer Cyclical": "🚗", "Consumer Defensive": "🛒", "Basic Materials": "🧪", "Real Estate": "🏠",
               "Communication Services": "📡", "Utilities": "⚡", "Energy": "🛢"}
MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
RULE = "━━━━━━━━━━━━━━━━━━"


def _icon(sector: str) -> str:
    return SECTOR_ICON.get(sector, "▫️")


def _symbol(b: Stock, url: str | None) -> str:
    """The symbol in bold, as a link to its dashboard page when there's a dashboard: Telegram can't colour text,
    but links show in the accent colour, so symbols stand out and open the stock."""
    sym = html.escape(b.symbol)
    if not url:
        return f"<b>{sym}</b>"
    return f'<b><a href="{html.escape(url)}#journal/stocks/{urllib.parse.quote(b.symbol)}.csv">{sym}</a></b>'


def _groups(stocks: list[Stock]) -> list[tuple[list[str], list[Stock]]]:
    """Stocks grouped by the exact set of strategies that fired, groups with more strategies first (broader
    agreement), then bigger groups; stocks keep their order (best score first) within a group."""
    groups: dict[tuple[str, ...], list[Stock]] = {}
    for b in stocks:
        groups.setdefault(tuple(b.strategies), []).append(b)
    return [(list(k), v) for k, v in sorted(groups.items(), key=lambda kv: (-len(kv[0]), -len(kv[1])))]


def _change(now: int | float, before: int | float | None, pct: bool = False) -> str:
    """'(▲3)', '(▼1)', '(=)' or '(new)' against the previous session; a percentage when pct."""
    if before is None:
        return "(new)"
    d = round((now - before) * 100) if pct else now - before
    return "(=)" if d == 0 else f"({'▲' if d > 0 else '▼'}{abs(d)}{'%' if pct else ''})"


def _tint(now: int | float, before: int | float | None, pct: bool = False) -> str:
    """A change as a coloured marker for Telegram, which can't colour text: '🟢 +3', '🔴 −1%', '⚪ 0' or
    '✨ new' (no previous value)."""
    if before is None:
        return "✨ new"
    d = round((now - before) * 100) if pct else now - before
    unit = "%" if pct else ""
    return f"⚪ 0{unit}" if d == 0 else (f"🟢 +{d}{unit}" if d > 0 else f"🔴 −{-d}{unit}")


def _squares(fraction: float, cells: int = 10) -> str:
    """A coloured bar: _squares(0.21) -> '🟩🟩⬜⬜⬜⬜⬜⬜⬜⬜'."""
    full = round(max(0.0, min(1.0, fraction)) * cells)
    return "🟩" * full + "⬜" * (cells - full)


def telegram_html(s: Summary, url: str | None = None) -> str:
    """Telegram HTML, laid out as a card. Telegram can't colour text, so colour comes from what it does tint:
    emoji (a green-square breadth bar, 🟢/🔴 change markers), links (symbols, see _symbol) and quote blocks
    (the new BUYs sit in one tinted card, a header per strategy set inside). Sections: the
    verdict counts and breadth (share of scanned stocks saying BUY) with its change, the hot sectors with their
    change, the new BUYs by strategy set (broadest first), the strongest BUYs with medals (🆕 on new ones), the
    dropped BUYs folded into an expandable quote, and links to the session page and the dashboard."""
    e = html.escape
    total = sum(s.actions.values()) or 1
    buy, watch, avoid = (s.actions.get(a, 0) for a in ("BUY", "WATCH", "AVOID"))
    share = buy / total
    lines = ["📈 <b>AI STOCK DAILY</b>", f"<i>{_day(s.date)} · NSE close</i>", RULE,
             f"🟢 <b>{buy}</b> BUY   🟡 <b>{watch}</b> WATCH   🔴 <b>{avoid}</b> AVOID",
             _squares(share),
             f"<b>{share:.0%}</b> of {total} stocks say BUY"
             + (f"  {_tint(share, s.prev_share, pct=True)}" if s.prev_share is not None else "")]
    if s.sectors:
        lines += ["", "🔥 <b>HOT SECTORS</b> <i>· BUYs</i>"]
        lines += [f"{_icon(k)} <code>{n:>3}</code> {e(k)}"
                  + (f"  {_tint(n, s.prev_sectors.get(k))}" if s.prev is not None else "")
                  for k, n in s.sectors[:3]]
    if s.prev is not None:
        lines += ["", f"🆕 <b>NEW BUYS</b> · {len(s.new)}"]
        if not s.new:
            lines.append("<i>No new BUYs since " + _day(s.prev)[:-5] + "</i>")
        # one tinted quote card (separate cards each shrink to their own width); inside, a header per strategy
        # set, then its stocks with the score first (fixed width) so scores form a column
        rows = []
        for strategies, group in _groups(s.new[:MAX_NEW]):
            rows += ([""] if rows else []) + [f"🔹 <b>{e(' · '.join(strategies) or 'no strategy')}</b>  <i>×{len(group)}</i>"]
            rows += [f"{_icon(b.sector)} <code>{_score(b.combined)}</code> {_symbol(b, url)} · <i>{e(b.name)}</i>"
                     for b in group]
        if rows:
            lines.append("<blockquote>" + "\n".join(rows) + "</blockquote>")
        if _more(s.new, MAX_NEW):
            lines.append(f"<i>+{_more(s.new, MAX_NEW)} more on the dashboard</i>")
    if s.buys:
        fresh = {b.symbol for b in s.new}
        lines += ["", "🏆 <b>STRONGEST BUYS</b>"]
        lines += [f"{MEDALS[k]} <code>{_score(b.combined)}</code> {_symbol(b, url)}{' 🆕' if b.symbol in fresh else ''}"
                  f" · <i>{e(b.name)}</i>" for k, b in enumerate(s.buys[:TOP])]
    if s.dropped:
        lines += ["", f"<blockquote expandable>➖ <b>No longer BUY</b> ({len(s.dropped)})\n{e(', '.join(s.dropped))}</blockquote>"]
    if url:
        session = f"{url}#journal/days/{s.date}.csv"
        lines += ["", f'👉 <a href="{e(session)}"><b>Open this session</b></a>  ·  <a href="{e(url)}">Dashboard</a>']
    return "\n".join(lines)


# ---------- image card (Telegram photo) ----------

# Strategy colours, shared by every card so a pill means the same thing each day.
STRATEGY_COLOURS = {"swing": "#3b82f6", "trend": "#10b981", "breakout": "#f59e0b", "leaders": "#a855f7",
                    "leaders-slope": "#ec4899", "ema-cross": "#06b6d4", "pullback": "#84cc16"}

CARD_CSS = """
:root { --bg: #0f1115; --panel: #171a21; --line: #262a33; --ink: #f2f3f5; --ink-2: #a3a8b3; --ink-3: #6b7280;
  --buy: #22c55e; --watch: #f59e0b; --avoid: #ef4444; --up: #22c55e; --down: #ef4444; --flat: #9ca3af; }
* { box-sizing: border-box; margin: 0; }
body { background: var(--bg); color: var(--ink); width: 540px; padding: 22px;
  font: 15px/1.4 Inter, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; font-variant-numeric: tabular-nums; }
.head { display: flex; justify-content: space-between; align-items: baseline; }
.brand { font-weight: 800; letter-spacing: .08em; font-size: 13px; color: var(--ink-2); }
.date { font-size: 13px; color: var(--ink-2); }
.hero { display: flex; align-items: baseline; gap: 10px; margin-top: 10px; }
.big { font-size: 44px; font-weight: 800; letter-spacing: -.02em; }
.big small { font-size: 22px; }
.sub { color: var(--ink-2); font-size: 14px; }
.chip { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; }
.chip.up { color: var(--up); background: rgba(34,197,94,.14); }
.chip.down { color: var(--down); background: rgba(239,68,68,.14); }
.chip.flat { color: var(--flat); background: rgba(156,163,175,.14); }
.chip.new { color: #60a5fa; background: rgba(96,165,250,.16); }
.split { display: flex; height: 10px; border-radius: 999px; overflow: hidden; margin: 12px 0 8px; background: var(--line); }
.split i { display: block; }
.legend { display: flex; gap: 16px; font-size: 13px; color: var(--ink-2); }
.legend b { color: var(--ink); }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }
section { margin-top: 18px; }
h2 { font-size: 12px; letter-spacing: .1em; color: var(--ink-3); font-weight: 700; margin-bottom: 8px;
  display: flex; justify-content: space-between; }
.sectors { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.sector { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; }
.sector .n { font-size: 22px; font-weight: 800; }
.sector .name { font-size: 12px; color: var(--ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.group { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 10px 12px; margin-bottom: 8px; }
.pills { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; margin-bottom: 6px; }
.pill { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px; color: #fff; }
.count { margin-left: auto; font-size: 12px; color: var(--ink-3); font-weight: 700; }
.row { display: grid; grid-template-columns: 1fr auto; align-items: center; padding: 6px 0; border-top: 1px solid var(--line); }
.row:first-of-type { border-top: none; }
.sym { font-weight: 800; font-size: 15px; letter-spacing: .01em; }
.meta { font-size: 12px; color: var(--ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 380px; }
.score { font-weight: 800; font-size: 14px; color: var(--buy); background: rgba(34,197,94,.12); padding: 3px 9px; border-radius: 8px; }
.rank { display: grid; grid-template-columns: 28px 1fr auto; align-items: center; gap: 8px; padding: 6px 0;
  border-top: 1px solid var(--line); }
.rank:first-of-type { border-top: none; }
.medal { width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; font-weight: 800;
  background: var(--line); color: var(--ink-2); }
.medal.m1 { background: #facc15; color: #1f1a00; } .medal.m2 { background: #d1d5db; color: #1f2937; }
.medal.m3 { background: #d97706; color: #1f1200; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 6px 12px; }
.dropped { margin-top: 16px; font-size: 12px; color: var(--ink-3); }
.dropped b { color: var(--ink-2); }
.foot { margin-top: 14px; font-size: 11px; color: var(--ink-3); text-align: right; }
"""


def _chip(now: float, before: float | None, pct: bool = False) -> str:
    """A change as a coloured chip: ▲3 green, ▼1% red, 0 grey, NEW blue."""
    if before is None:
        return '<span class="chip new">NEW</span>'
    d = round((now - before) * 100) if pct else now - before
    unit = "%" if pct else ""
    if d == 0:
        return f'<span class="chip flat">0{unit}</span>'
    return f'<span class="chip {"up" if d > 0 else "down"}">{"▲" if d > 0 else "▼"}{abs(d)}{unit}</span>'


def card_html(s: Summary) -> str:
    """The alert as a self-contained HTML card (rendered to an image for Telegram, see card_png): breadth with
    its change and a BUY/WATCH/AVOID split bar, hot sectors as tiles, new BUYs grouped under colour-coded
    strategy pills, the strongest BUYs ranked, dropped BUYs in a muted line."""
    e = html.escape
    total = sum(s.actions.values()) or 1
    buy, watch, avoid = (s.actions.get(a, 0) for a in ("BUY", "WATCH", "AVOID"))
    share = buy / total
    split = "".join(f'<i style="width:{n / total * 100:.2f}%;background:var(--{k})"></i>'
                    for k, n in (("buy", buy), ("watch", watch), ("avoid", avoid)))
    out = [f'<div class="head"><span class="brand">AI STOCK DAILY</span><span class="date">{e(_day(s.date))} · NSE close</span></div>',
           f'<div class="hero"><span class="big">{share * 100:.0f}<small>%</small></span>'
           f'<span class="sub">of {total} stocks say BUY</span>'
           + (_chip(share, s.prev_share, pct=True) if s.prev_share is not None else "") + "</div>",
           f'<div class="split">{split}</div>',
           '<div class="legend">' + "".join(f'<span><span class="dot" style="background:var(--{k})"></span><b>{n}</b> {k.upper()}</span>'
                                            for k, n in (("buy", buy), ("watch", watch), ("avoid", avoid))) + "</div>"]
    if s.sectors:
        tiles = "".join(f'<div class="sector"><div class="n">{n} '
                        + (_chip(n, s.prev_sectors.get(k)) if s.prev is not None else "")
                        + f'</div><div class="name">{e(k)}</div></div>' for k, n in s.sectors[:3])
        out.append(f'<section><h2><span>HOT SECTORS</span><span>BUYs</span></h2><div class="sectors">{tiles}</div></section>')
    if s.prev is not None:
        groups = []
        for strategies, group in _groups(s.new[:MAX_NEW]):
            pills = "".join(f'<span class="pill" style="background:{STRATEGY_COLOURS.get(x, "#4b5563")}">{e(x)}</span>'
                            for x in strategies)
            rows = "".join(f'<div class="row"><div><div class="sym">{e(b.symbol)}</div>'
                           f'<div class="meta">{e(b.name)} · {e(b.sector)}</div></div>'
                           f'<span class="score">{_score(b.combined)}</span></div>' for b in group)
            groups.append(f'<div class="group"><div class="pills">{pills}<span class="count">×{len(group)}</span></div>{rows}</div>')
        more = f'<div class="sub">+{_more(s.new, MAX_NEW)} more on the dashboard</div>' if _more(s.new, MAX_NEW) else ""
        body = "".join(groups) or f'<div class="sub">No new BUYs since {e(_day(s.prev)[:-5])}</div>'
        out.append(f'<section><h2><span>NEW BUYS</span><span>{len(s.new)}</span></h2>{body}{more}</section>')
    if s.buys:
        fresh = {b.symbol for b in s.new}
        ranks = "".join(f'<div class="rank"><span class="medal m{k + 1}">{k + 1}</span>'
                        f'<div><span class="sym">{e(b.symbol)}</span> '
                        + ('<span class="chip new">NEW</span>' if b.symbol in fresh else "")
                        + f'<div class="meta">{e(b.name)} · {e(b.sector)}</div></div>'
                        f'<span class="score">{_score(b.combined)}</span></div>' for k, b in enumerate(s.buys[:TOP]))
        out.append(f'<section><h2><span>STRONGEST BUYS</span><span>score</span></h2><div class="panel">{ranks}</div></section>')
    if s.dropped:
        out.append(f'<div class="dropped"><b>No longer BUY ({len(s.dropped)}):</b> {e(", ".join(s.dropped))}</div>')
    out.append('<div class="foot">aistock · rules decide, scores rank</div>')
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;800&display=swap">'
            f"<style>{CARD_CSS}</style></head><body>" + "".join(out)
            + "<script>document.fonts.ready.then(() => { document.body.dataset.shown = 'card'; });</script></body></html>")


def card_png(s: Summary) -> bytes | None:
    """The card as a PNG via headless Chrome; None when Chrome isn't available (callers fall back to text)."""
    from aistock.site import screenshots

    try:
        return screenshots.render(card_html(s))
    except Exception as err:  # no Chrome, or it failed: the text alert still goes out
        print(f"card image skipped: {type(err).__name__}: {err}")
        return None


def caption_html(s: Summary, url: str | None = None) -> str:
    """The photo's caption: what the image shows in one line, plus the links an image can't hold."""
    e = html.escape
    line = f"📈 <b>AI Stock · {e(_day(s.date))}</b> · {len(s.buys)} BUYs" + (f", {len(s.new)} new" if s.prev else "")
    if url:
        line += (f'\n👉 <a href="{e(url)}#journal/days/{s.date}.csv">Open this session</a>'
                 f'  ·  <a href="{e(url)}">Dashboard</a>')
    return line


def markdown(s: Summary, url: str | None = None) -> str:
    """GitHub markdown: the issue comment, new and top BUYs as tables."""
    lines = [f"### 📈 AI Stock · {_day(s.date)}", f"🟢 **{s.actions.get('BUY', 0)}** BUY · 🟡 {s.actions.get('WATCH', 0)} WATCH · "
             f"🔴 {s.actions.get('AVOID', 0)} AVOID"] + ([f"🔥 {_hot(s)}"] if s.sectors else [])
    if s.new:
        lines += ["", f"**🆕 New BUYs** ({len(s.new)}, ➖ {len(s.dropped)} dropped)", "", "| Stock | Name | Sector | Score | Strategies |", "|---|---|---|---:|---|"]
        lines += [f"| **{b.symbol}** | {b.name} | {b.sector} | {_score(b.combined)} | {_strategies(b)} |"
                  for b in s.new[:MAX_NEW]]
        if _more(s.new, MAX_NEW):
            lines.append(f"\n_+{_more(s.new, MAX_NEW)} more on the dashboard_")
    if s.buys:
        lines += ["", f"**🏆 Top {min(TOP, len(s.buys))} by score**", "", "| Stock | Score | Sector |", "|---|---:|---|"]
        lines += [f"| **{b.symbol}** | {_score(b.combined)} | {b.sector} |" for b in s.buys[:TOP]]
    if s.dropped:
        lines += ["", f"**➖ Dropped** {_names(s.dropped, MAX_DROPPED)}"]
    if url:
        lines += ["", f"[Open dashboard →]({url})"]
    return "\n".join(lines)


def telegram(text: str, token: str, chat_id: str) -> None:
    """Send `text` (Telegram HTML, see telegram_html) from the bot to the chat (Bot API sendMessage)."""
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=30) as r:
        if not json.load(r).get("ok"):
            raise RuntimeError("Telegram refused the message")


def telegram_photo(png: bytes, caption: str, token: str, chat_id: str) -> None:
    """Send `png` with an HTML `caption` from the bot to the chat (Bot API sendPhoto, multipart upload)."""
    boundary = uuid.uuid4().hex
    part = lambda name, value, extra="": (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"{extra}\r\n'
                                          + ("Content-Type: image/png\r\n" if extra else "") + "\r\n").encode() + value + b"\r\n"
    body = (part("chat_id", chat_id.encode()) + part("caption", caption.encode()) + part("parse_mode", b"HTML")
            + part("photo", png, '; filename="alert.png"') + f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", body,
                                     {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=60) as r:
        if not json.load(r).get("ok"):
            raise RuntimeError("Telegram refused the photo")


def _telegram(s: Summary, url: str | None, token: str, chat_id: str) -> str:
    """The card image with a caption holding the links; the text card when no image could be made."""
    png = card_png(s)
    if png:
        telegram_photo(png, caption_html(s, url), token, chat_id)
        return "sent (card image)"
    telegram(telegram_html(s, url), token, chat_id)
    return "sent (text)"


def email(text: str, user: str, password: str, to: str, server: str = "smtp.gmail.com", port: int = 465) -> None:
    """Send `text` as a plain-text email over SMTP with SSL; the subject is its first line."""
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = text.splitlines()[0], user, to
    msg.set_content(text)
    with smtplib.SMTP_SSL(server, port, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)


def _gh(*args) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()


def github_issue(text: str, title: str = ISSUE_TITLE) -> str:
    """Comment `text` on the open issue titled `title`, creating it first if needed; returns the comment URL."""
    found = json.loads(_gh("issue", "list", "--state", "open", "--search", f'"{title}" in:title',
                           "--json", "number,title"))
    number = next((i["number"] for i in found if i["title"] == title), None)
    if number is None:
        created = _gh("issue", "create", "--title", title, "--body",
                      "One comment per session from the Daily scan workflow (`aistock alert`). "
                      "Subscribe to this issue to get them as notifications.")
        number = int(created.rstrip("/").split("/")[-1])
    return _gh("issue", "comment", str(number), "--body", text)


def send(s: Summary, url: str | None = None, to_telegram: bool = True, to_issue: bool = True,
         to_email: bool = True) -> list[str]:
    """Send to each channel that is enabled and configured (secrets in the environment); returns what
    happened, one line per channel. A failing channel is reported and the others still go out."""
    env = os.environ.get
    channels = []
    if to_telegram:
        channels.append(("Telegram", ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
                         lambda: _telegram(s, url, env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID"))))
    if to_email:
        channels.append(("Email", ("SMTP_USERNAME", "SMTP_PASSWORD"),
                         lambda: email(plain(s, url), env("SMTP_USERNAME"), env("SMTP_PASSWORD"),
                                       env("ALERT_EMAIL_TO") or env("SMTP_USERNAME"),
                                       env("SMTP_SERVER") or "smtp.gmail.com", int(env("SMTP_PORT") or 465))
                         or f"sent to {env('ALERT_EMAIL_TO') or env('SMTP_USERNAME')}"))
    if to_issue:
        channels.append(("GitHub issue", (), lambda: github_issue(markdown(s, url))))
    done = []
    for name, needed, go in channels:
        if not all(env(k) for k in needed):
            done.append(f"{name}: skipped ({' / '.join(needed)} not set)")
            continue
        try:
            done.append(f"{name}: {go()}")
        except Exception as e:  # e.g. a revoked token: report it, still try the other channels
            done.append(f"{name}: failed ({type(e).__name__}: {e})")
    return done
