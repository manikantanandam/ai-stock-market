"""Daily alert: a short summary of one journaled session, sent to Telegram, by email and as a comment on a
"Daily alerts" GitHub issue (`aistock alert`; run at the end of the Daily scan workflow, and by hand for any
date through the Send alert workflow).

Telegram gets the summary as an image card (`card_html`, rendered by headless Chrome, sent with `caption_html`
holding the links), falling back to `telegram_html` text when no image can be made.
The commodity session (`commodity_summary`, journal/commodities/days) follows as a second Telegram message
with its own card (`commodity_card_html`). The saved LLM notes (journal/notes, journal/commodities/notes,
written by `scan --notes`) of the stocks and contracts shown go along as bullet points (one per sentence)
under each row of the stock and commodity cards; as text after the text card without Chrome.

`summary` reads the session's journal files only (no LLM call here, decision 0001): the BUY signals with each
stock's company name, sector, combined score and strategies, which of them are new since the previous session, and
which BUYs were dropped. Paper accounts are left to the dashboard. Each channel gets its own rendering of the
same summary: `telegram_html` (a block per new BUY, the top BUYs aligned), `markdown` (tables, for the issue)
and `plain` (email and the terminal).
"""
import html
import json
import re
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
TELEGRAM_LIMIT = 4096  # characters per Telegram message


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
    notes: dict[str, str] = field(default_factory=dict)  # LLM note per symbol (journal/notes), when one was saved

    def mentioned(self) -> list[Stock]:
        """The stocks the card shows: the listed new BUYs, then the strongest BUYs not already among them."""
        shown = {}
        for b in (self.new[:MAX_NEW] if self.prev else []) + self.buys[:TOP]:
            shown.setdefault(b.symbol, b)
        return list(shown.values())


def _names(symbols: list[str], limit: int) -> str:
    more = len(symbols) - limit
    return ", ".join(symbols[:limit]) + (f" +{more} more" if more > 0 else "")


def _short(name: str) -> str:
    """Company name without the legal suffix: "Mphasis Limited" -> "Mphasis"."""
    for suffix in (" Limited", " Ltd.", " Ltd"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _notes(path: Path) -> dict[str, str]:
    """The first saved LLM note per symbol in a notes CSV; empty when the session has none."""
    if not path.exists():
        return {}
    df = pd.read_csv(path).dropna(subset=["note"])
    return {r["symbol"]: r["note"] for _, r in df.drop_duplicates("symbol").iterrows()}


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
    return Summary(date, prev, buys, new, dropped, actions, sectors, prev_share, prev_sectors,
                   _notes(root / "journal" / "notes" / f"{date}.csv"))


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
    if stock_notes(s):
        lines += ["", "Notes (LLM, explains the rules' BUY)"]
        for head, note in stock_notes(s):
            lines += ["", f"  {head}", f"  {note}"]
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
        listed = {b.symbol for b in s.new[:MAX_NEW]} if s.prev is not None else set()
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
                    "leaders-slope": "#ec4899", "ema-cross": "#06b6d4", "pullback": "#84cc16",
                    "trend-long": "#14b8a6", "channel": "#f97316"}  # the last two are commodity presets

CARD_CSS = """
:root { --bg: #0f1115; --panel: #171a21; --line: #262a33; --ink: #f2f3f5; --ink-2: #a3a8b3; --ink-3: #6b7280;
  --buy: #22c55e; --watch: #f59e0b; --avoid: #ef4444; --up: #22c55e; --down: #ef4444; --flat: #9ca3af; }
* { box-sizing: border-box; margin: 0; }
body { background: var(--bg); color: var(--ink); padding: 18px 20px;
  font: 15px/1.4 Inter, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; font-variant-numeric: tabular-nums; }
.head { display: flex; justify-content: space-between; align-items: baseline; }
.brand { font-weight: 800; letter-spacing: .08em; font-size: 13px; color: var(--ink-2); }
.date { font-size: 13px; color: var(--ink-2); }
.hero { display: flex; align-items: baseline; gap: 10px; margin-top: 4px; }
.big { font-size: 38px; font-weight: 800; letter-spacing: -.02em; line-height: 1.1; }
.big small { font-size: 22px; }
.sub { color: var(--ink-2); font-size: 14px; }
.chip { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; }
.chip.up { color: var(--up); background: rgba(34,197,94,.14); }
.chip.down { color: var(--down); background: rgba(239,68,68,.14); }
.chip.flat { color: var(--flat); background: rgba(156,163,175,.14); }
.chip.new { color: #60a5fa; background: rgba(96,165,250,.16); }
.split { display: flex; height: 10px; border-radius: 999px; overflow: hidden; margin: 8px 0 6px; background: var(--line); }
.split i { display: block; }
.legend { display: flex; gap: 16px; font-size: 13px; color: var(--ink-2); }
.legend b { color: var(--ink); }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }
section { margin-top: 10px; }
h2 { font-size: 12px; letter-spacing: .1em; color: var(--ink-3); font-weight: 700; margin-bottom: 6px;
  display: flex; justify-content: space-between; }
.sectors { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.sector { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 6px 10px; }
.sector { display: flex; align-items: center; gap: 6px; min-width: 0; }
.sector .n { font-size: 17px; font-weight: 800; }
.sector .name { font-size: 12px; color: var(--ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.group { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 7px 12px; margin-bottom: 6px; }
.pills { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; margin-bottom: 4px; }
.pill { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px; color: #fff; }
.count { margin-left: auto; font-size: 12px; color: var(--ink-3); font-weight: 700; }
.row { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 10px; padding: 3px 0;
  border-top: 1px solid var(--line); }
.line { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.row:first-of-type { border-top: none; }
.sym { font-weight: 800; font-size: 15px; letter-spacing: .01em; }
.meta { font-size: 12px; color: var(--ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.score { font-weight: 800; font-size: 13px; color: var(--buy); background: rgba(34,197,94,.12); padding: 2px 8px; border-radius: 7px; }
.rank { display: grid; grid-template-columns: 24px 1fr auto; align-items: center; gap: 8px; padding: 3px 0;
  border-top: 1px solid var(--line); }
.rank:first-of-type { border-top: none; }
.medal { width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; font-weight: 800;
  background: var(--line); color: var(--ink-2); }
.medal.m1 { background: #facc15; color: #1f1a00; } .medal.m2 { background: #d1d5db; color: #1f2937; }
.medal.m3 { background: #d97706; color: #1f1200; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 6px 12px; }
.dropped { margin-top: 10px; font-size: 12px; color: var(--ink-3); }
.dropped b { color: var(--ink-2); }
.hot { color: #fb923c; font-weight: 700; }
.hot::before { content: ""; display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #fb923c;
  margin-right: 5px; vertical-align: 1px; }
.note { margin-top: 10px; padding: 8px 12px; border-radius: 12px; background: var(--panel); border: 1px solid var(--line);
  font-size: 12px; line-height: 1.5; color: var(--ink-2); }
.note b { color: var(--ink); }
.scale { display: flex; height: 6px; border-radius: 999px; margin: 8px 0 4px;
  background: linear-gradient(90deg, var(--avoid), var(--watch) 50%, var(--buy)); }
.ticks { display: flex; justify-content: space-between; font-size: 11px; color: var(--ink-3); }
.points { margin: 1px 0 5px; padding-left: 16px; font-size: 11.5px; line-height: 1.4; color: var(--ink-3); }
.points li { margin-top: 1px; }
.points li::marker { color: var(--ink-3); }
.points li.risk { color: #fca5a5; }
.points li.risk::marker { color: var(--avoid); }
.points li.target { color: #86efac; }
.points li.target::marker { color: var(--buy); }
.rank + .points { margin-left: 32px; }
.foot { margin-top: 8px; font-size: 11px; color: var(--ink-3); text-align: right; }
"""


# Short sector names for the narrow sector tiles (full names elsewhere)
SHORT_SECTOR = {"Financial Services": "Financials", "Consumer Cyclical": "Cons. Cyclical",
                "Consumer Defensive": "Cons. Defensive", "Communication Services": "Comm. Services",
                "Basic Materials": "Materials"}


def _chip(now: float, before: float | None, pct: bool = False) -> str:
    """A change as a coloured chip: ▲3 green, ▼1% red, 0 grey, NEW blue."""
    if before is None:
        return '<span class="chip new">NEW</span>'
    d = round((now - before) * 100) if pct else now - before
    unit = "%" if pct else ""
    if d == 0:
        return f'<span class="chip flat">0{unit}</span>'
    return f'<span class="chip {"up" if d > 0 else "down"}">{"▲" if d > 0 else "▼"}{abs(d)}{unit}</span>'


def score_note() -> str:
    """How the score is made, from the live weights and strategy minimums (so the card can't drift from them)."""
    from aistock.strategy.signal import Config
    from aistock.strategy.strategies import PRESETS

    c = Config()
    parts = [(c.w_technical, "technical"), (c.w_fundamental, "fundamentals"), (c.w_sentiment, "news sentiment")]
    mins = [f"{name} +{st.min_combined:.2f}" for name, st in PRESETS.items() if st.min_combined is not None]
    return ("<b>Score</b> −1 to +1 = " + " + ".join(f"{w:.0%} {label}" for w, label in parts)
            + " (each −1 to +1; news 0 if not fetched). Strategies' rules decide BUYs; the score ranks them"
            + (f" ({', '.join(m.replace(' +', ' needs ≥ +') for m in mins)})" if mins else "") + ".")


def _sector(sector: str, hot: set[str]) -> str:
    """A row's sector, in the hot colour when it is one of the session's hot sectors (most BUYs)."""
    return f'<span class="hot">{html.escape(sector)}</span>' if sector in hot else html.escape(sector)


def _points(s: Summary, symbol: str) -> str:
    """A stock's saved LLM note under its card row, as bullet points (one per sentence)."""
    return points_html(s.notes[symbol]) if symbol in s.notes else ""


def card_html(s: Summary, notes: bool = True) -> str:
    """The alert as a self-contained HTML card (rendered to an image for Telegram, see card_png): breadth with
    its change and a BUY/WATCH/AVOID split bar, hot sectors as tiles, new BUYs grouped under colour-coded
    strategy pills, the strongest BUYs ranked (a row's sector in orange when it is a hot sector), each with the
    saved LLM note as bullet points (once, so a new BUY among the strongest isn't repeated), and a note
    on what the score means. notes=False: the card alone, short enough for one image; with the notes it is
    tall and goes out in slices (see TALLEST). Dropped BUYs go in the caption."""
    points = _points if notes else lambda s, symbol: ""
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
    hot = {k for k, _ in s.sectors[:3]}
    if s.sectors:
        tiles = "".join(f'<div class="sector"><span class="n">{n}</span>'
                        + (_chip(n, s.prev_sectors.get(k)) if s.prev is not None else "")
                        + f'<span class="name">{e(SHORT_SECTOR.get(k, k))}</span></div>' for k, n in s.sectors[:3])
        out.append(f'<section><h2><span class="hot">HOT SECTORS</span><span>BUYs</span></h2><div class="sectors">{tiles}</div></section>')
    if s.prev is not None:
        groups = []
        for strategies, group in _groups(s.new[:MAX_NEW]):
            pills = "".join(f'<span class="pill" style="background:{STRATEGY_COLOURS.get(x, "#4b5563")}">{e(x)}</span>'
                            for x in strategies)
            rows = "".join(f'<div class="row"><div class="line"><span class="sym">{e(b.symbol)}</span>'
                           f'<span class="meta">{e(b.name)} · {_sector(b.sector, hot)}</span></div>'
                           f'<span class="score">{_score(b.combined)}</span></div>' + points(s, b.symbol) for b in group)
            groups.append(f'<div class="group"><div class="pills">{pills}<span class="count">×{len(group)}</span></div>{rows}</div>')
        more = f'<div class="sub">+{_more(s.new, MAX_NEW)} more on the dashboard</div>' if _more(s.new, MAX_NEW) else ""
        body = "".join(groups) or f'<div class="sub">No new BUYs since {e(_day(s.prev)[:-5])}</div>'
        out.append(f'<section><h2><span>NEW BUYS</span><span>{len(s.new)}</span></h2>{body}{more}</section>')
    if s.buys:
        fresh = {b.symbol for b in s.new}
        listed = {b.symbol for b in s.new[:MAX_NEW]} if s.prev is not None else set()
        ranks = "".join(f'<div class="rank"><span class="medal m{k + 1}">{k + 1}</span>'
                        f'<div class="line"><span class="sym">{e(b.symbol)}</span>'
                        + ('<span class="chip new">NEW</span>' if b.symbol in fresh else "")
                        + f'<span class="meta">{e(b.name)} · {_sector(b.sector, hot)}</span></div>'
                        f'<span class="score">{_score(b.combined)}</span></div>'
                        + ("" if b.symbol in listed else points(s, b.symbol)) for k, b in enumerate(s.buys[:TOP]))
        out.append(f'<section><h2><span>STRONGEST BUYS</span><span>score</span></h2><div class="panel">{ranks}</div></section>')
    out.append(f'<div class="note">{score_note()}<div class="scale"></div>'
               '<div class="ticks"><span>−1 bearish</span><span>0 neutral</span><span>+1 bullish</span></div></div>')
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;800&display=swap">'
            f"<style>{CARD_CSS}</style></head><body>" + "".join(out)
            + "<script>document.fonts.ready.then(() => { document.body.dataset.shown = 'card'; });</script></body></html>")


# Telegram shrinks a photo to 2560 px on its longer side, so a tall card would come out narrow and blurry:
# cards go out as slices at most this tall (at width 1200), cut between rows, sent as one album
TALLEST = 2560
BREAKS = "section, .group, .row, .rank, .citem, .note"


def card_png(s: Summary, notes: bool = True) -> list[bytes] | None:
    """The card via headless Chrome: one PNG without the notes, PNG slices with them; None when Chrome isn't
    available (callers fall back to text)."""
    from aistock.site import screenshots

    try:
        return screenshots.render(card_html(s, notes), tallest=TALLEST if notes else None, breaks=BREAKS)
    except Exception as err:  # no Chrome, or it failed: the text alert still goes out
        print(f"card image skipped: {type(err).__name__}: {err}")
        return None


def caption_html(s: Summary, url: str | None = None) -> str:
    """The photo's caption: what the image shows in one line, the dropped BUYs, and the links an image can't
    hold."""
    e = html.escape
    line = f"📈 <b>AI Stock · {e(_day(s.date))}</b> · {len(s.buys)} BUYs" + (f", {len(s.new)} new" if s.prev else "")
    if s.dropped:  # kept off the image so it stays short enough to fill Telegram's preview
        line += f"\n➖ <i>No longer BUY ({len(s.dropped)}): {e(', '.join(s.dropped))}</i>"
    if url:
        line += (f'\n👉 <a href="{e(url)}#journal/days/{s.date}.csv">Open this session</a>'
                 f'  ·  <a href="{e(url)}">Dashboard</a>')
    return line


# ---------- commodities: their own card, sent as a second Telegram message ----------

ACTION_ORDER = ["BUY", "SELL", "MIXED", "EXIT", "HOLD", "WATCH"]  # what to act on first
ACTION_COLOURS = {"BUY": "#22c55e", "SELL": "#ef4444", "MIXED": "#a855f7", "EXIT": "#f59e0b", "HOLD": "#3b82f6",
                  "WATCH": "#9ca3af"}
ACTION_COLUMNS = {"BUY": ["buys"], "SELL": ["sells"], "MIXED": ["buys", "sells"], "EXIT": ["exits"], "HOLD": ["holds"]}


@dataclass
class Contract:
    symbol: str
    name: str
    group: str
    unit: str
    action: str               # BUY, SELL, MIXED, EXIT, HOLD or WATCH
    side: str                 # long / short, "" for WATCH and MIXED
    strategies: list[str]     # the presets behind the action
    combined: float
    close: float              # MCX quote units (rupees per `unit`)
    prev_action: str | None = None
    prev_close: float | None = None


@dataclass
class Commodities:
    date: str
    prev: str | None
    contracts: list[Contract]  # actionable first (ACTION_ORDER), then journal order
    notes: dict[str, str] = field(default_factory=dict)  # LLM note per contract (journal/commodities/notes)


def commodity_summary(root: Path = ROOT, date: str | None = None) -> Commodities | None:
    """The latest commodity session on or before `date` (default: the latest one), with each contract's action
    and the previous session's action and close; None if there is none. Futures sessions follow New York, so
    they can lag the stock session by a day (US holidays)."""
    days = [p.stem for p in sorted((root / "journal" / "commodities" / "days").glob("*.csv"))]
    days = [d for d in days if date is None or d <= date]
    if not days:
        return None
    read = lambda d: pd.read_csv(root / "journal" / "commodities" / "days" / f"{d}.csv")
    today, prev = read(days[-1]), (days[-2] if len(days) > 1 else None)
    before = read(prev).set_index("symbol") if prev else pd.DataFrame()
    text = lambda r, col: r.get(col) if isinstance(r.get(col), str) else ""
    contracts = []
    for _, r in today.iterrows():
        names = [x for col in ACTION_COLUMNS.get(r["action"], []) for x in text(r, col).split(";") if x]
        old = before.loc[r["symbol"]] if r["symbol"] in before.index else None
        contracts.append(Contract(r["symbol"], text(r, "name") or r["symbol"], text(r, "group"), text(r, "unit"),
                                  r["action"], text(r, "side") if r["action"] != "MIXED" else "",
                                  list(dict.fromkeys(x.split(":")[0] for x in names)), float(r["combined"]),
                                  float(r["close"]), old["action"] if old is not None else None,
                                  float(old["close"]) if old is not None else None))
    order = lambda c: ACTION_ORDER.index(c.action) if c.action in ACTION_ORDER else len(ACTION_ORDER)
    return Commodities(days[-1], prev, sorted(contracts, key=order),
                       _notes(root / "journal" / "commodities" / "notes" / f"{days[-1]}.csv"))


def _verdict(c: Contract) -> str:
    return c.action + (f" {c.side.upper()}" if c.side and c.action in ("EXIT", "HOLD") else "")


def commodity_note() -> str:
    """What the verdicts and the score mean, from the live commodity weights."""
    from aistock.commodity.signal import Config

    c = Config()
    return ("<b>BUY / SELL</b> enter long / short at the next open · <b>EXIT</b> a position closed on the latest "
            "bar · <b>HOLD</b> a position still open · <b>WATCH</b> nothing to do. <b>Score</b> −1 to +1 = "
            f"{c.w_technical:.0%} technical + {c.w_sentiment:.0%} news sentiment. Prices in rupees per MCX unit, "
            "from Yahoo futures × USDINR.")


def commodity_card_html(c: Commodities) -> str:
    """The commodity session as an HTML card: verdict counts, then one row per contract (action badge with its
    side, the strategies behind it, close with its change, score) with its saved LLM note as bullet points,
    actionable contracts first."""
    e = html.escape
    counts = {a: sum(x.action == a for x in c.contracts) for a in ACTION_ORDER}
    legend = "".join(f'<span><span class="dot" style="background:{ACTION_COLOURS[a]}"></span><b>{n}</b> {a}</span>'
                     for a, n in counts.items() if n)
    rows = []
    for x in c.contracts:
        changed = (f'<span class="chip new">was {e(x.prev_action)}</span>'
                   if x.prev_action is not None and x.prev_action != x.action else "")
        move = ""
        if x.prev_close:
            pct = (x.close / x.prev_close - 1) * 100
            kind = "flat" if round(pct, 1) == 0 else "up" if pct > 0 else "down"
            move = f'<span class="chip {kind}">{"▲" if pct > 0 else "▼" if pct < 0 else ""}{abs(pct):.1f}%</span>'
        pills = "".join(f'<span class="pill" style="background:{STRATEGY_COLOURS.get(n, "#4b5563")}">{e(n)}</span>'
                        for n in x.strategies)
        colour = ACTION_COLOURS.get(x.action, "#4b5563")
        tone = "score" if x.combined >= 0 else "score neg"
        rows.append(f'<div class="citem"><div class="crow"><div><div class="line"><span class="sym">{e(x.name)}</span>{changed}</div>'
                    f'<div class="meta">{e(x.group)} · ₹{x.close:,.0f} / {e(x.unit)} {move}</div>'
                    + (f'<div class="pills">{pills}</div>' if pills else "") + "</div>"
                    f'<div class="right"><span class="badge" style="color:{colour};border-color:{colour}">{e(_verdict(x))}</span>'
                    f'<span class="{tone}">{_score(x.combined)}</span></div></div>'
                    + (points_html(c.notes[x.symbol]) if x.symbol in c.notes else "") + "</div>")
    since = f" · vs {e(_day(c.prev)[:-5])}" if c.prev else ""
    out = [f'<div class="head"><span class="brand">AI COMMODITIES</span><span class="date">{e(_day(c.date))} · futures close</span></div>',
           f'<div class="hero"><span class="big">{len(c.contracts)}</span><span class="sub">contracts{since}</span></div>',
           f'<div class="legend">{legend}</div>',
           f'<section><div class="panel">{"".join(rows)}</div></section>',
           f'<div class="note">{commodity_note()}</div>']
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;800&display=swap">'
            f"<style>{CARD_CSS}{COMMODITY_CSS}</style></head><body>" + "".join(out)
            + "<script>document.fonts.ready.then(() => { document.body.dataset.shown = 'card'; });</script></body></html>")


COMMODITY_CSS = """
.legend { margin-top: 8px; }
.citem { border-top: 1px solid var(--line); }
.citem:first-child { border-top: none; }
.crow { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 10px; padding: 8px 0; }
.citem .points { margin-top: -2px; }
.crow .meta { margin-top: 2px; }
.crow .pills { margin: 5px 0 0; }
.right { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
.badge { font-size: 12px; font-weight: 800; letter-spacing: .04em; padding: 2px 9px; border: 1.5px solid; border-radius: 7px; }
.score.neg { color: var(--avoid); background: rgba(239,68,68,.12); }
"""


def commodity_png(c: Commodities) -> list[bytes] | None:
    """The commodity card as one PNG (six contracts stay readable after Telegram's shrink); None when Chrome
    isn't available."""
    from aistock.site import screenshots

    try:
        return screenshots.render(commodity_card_html(c))
    except Exception as err:
        print(f"commodity card image skipped: {type(err).__name__}: {err}")
        return None


def commodity_caption(c: Commodities, url: str | None = None) -> str:
    """The commodity photo's caption: the session, its actionable contracts, the link to its dashboard page."""
    e = html.escape
    act = [x for x in c.contracts if x.action in ("BUY", "SELL", "MIXED", "EXIT")]
    line = f"🛢 <b>AI Commodities · {e(_day(c.date))}</b>"
    line += ("\n⚡ " + ", ".join(f"{e(x.name)} {x.action}" for x in act)) if act else " · no new entries or exits"
    if url:
        line += f'\n👉 <a href="{e(url)}#journal/commodities/days/{c.date}.csv">Open this session</a>'
    return line


def commodity_text(c: Commodities, url: str | None = None) -> str:
    """Telegram HTML text when no image can be made: one line per contract."""
    e = html.escape
    rows = [f"<b>{e(x.name)}</b> {e(_verdict(x))} {_score(x.combined)} · ₹{x.close:,.0f}/{e(x.unit)}"
            + (f" · <i>{e(', '.join(x.strategies))}</i>" if x.strategies else "") for x in c.contracts]
    return commodity_caption(c, url).split("\n👉")[0] + "\n\n" + "\n".join(rows) + (
        f'\n\n👉 <a href="{e(url)}#journal/commodities/days/{c.date}.csv">Open this session</a>' if url else "")


# ---------- LLM notes: bullet points under each card row ----------

# A sentence ends at ., ! or ? before a capital, unless the period closes a one-letter initial ("K.P.R. Mill").
SENTENCE = re.compile(r"(?<![\s.(][A-Z]\.)(?<=[.!?])\s+(?=[A-Z])")


def points(note: str) -> list[str]:
    """The note's sentences, each a bullet point on the cards; nothing is dropped or shortened."""
    return [x for x in SENTENCE.split(note.strip()) if x]


RISK = re.compile(r"\b(risks?|risky|loss)\b", re.I)
TARGET = re.compile(r"\btargets?\b", re.I)


def tone(point: str) -> str:
    """A bullet's colour on the cards: red for a risk (or loss) sentence, green for the target (upside), else
    none; a sentence naming both is a risk."""
    return "risk" if RISK.search(point) else "target" if TARGET.search(point) else ""


def points_html(note: str) -> str:
    return '<ul class="points">' + "".join((f'<li class="{tone(x)}">' if tone(x) else "<li>")
                                           + html.escape(x) + "</li>" for x in points(note)) + "</ul>"



def notes_html(title: str, items: list[tuple[str, str]]) -> list[str]:
    """Telegram HTML messages holding each (heading, note) under `title`, every note in its own expandable
    quote; split into as many messages as Telegram's length limit needs. Empty when there are no notes."""
    e = html.escape
    blocks = [f"<b>{e(head)}</b>\n<blockquote expandable>{e(note)}</blockquote>" for head, note in items]
    messages, current = [], f"🤖 <b>{e(title)}</b>"
    for block in blocks:
        if len(current) + 2 + len(block) > TELEGRAM_LIMIT:
            messages.append(current)
            current = f"🤖 <b>{e(title)}</b> (cont.)"
        current += "\n\n" + block
    return messages + [current] if blocks else []


def stock_notes(s: Summary) -> list[tuple[str, str]]:
    """(heading, note) for each stock on the card that has a saved note, in card order."""
    return [(f"{b.symbol} · {b.name} · {_score(b.combined)}", s.notes[b.symbol])
            for b in s.mentioned() if b.symbol in s.notes]


def commodity_notes(c: Commodities) -> list[tuple[str, str]]:
    return [(f"{x.name} · {_verdict(x)} · {_score(x.combined)}", c.notes[x.symbol])
            for x in c.contracts if x.symbol in c.notes]


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
    if stock_notes(s):
        lines += ["", "**🤖 Notes** (LLM, explains the rules' BUY)", ""]
        lines += [f"<details><summary>{head}</summary>\n\n{note}\n</details>" for head, note in stock_notes(s)]
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


def telegram_photo(pngs: list[bytes], caption: str, token: str, chat_id: str) -> None:
    """Send the `pngs` with an HTML `caption` from the bot to the chat: one as a photo (Bot API sendPhoto),
    several as an album with the caption on the first (sendMediaGroup); multipart upload."""
    boundary = uuid.uuid4().hex
    part = lambda name, value, extra="": (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"{extra}\r\n'
                                          + ("Content-Type: image/png\r\n" if extra else "") + "\r\n").encode() + value + b"\r\n"
    body = part("chat_id", chat_id.encode())
    if len(pngs) == 1:
        method = "sendPhoto"
        body += part("caption", caption.encode()) + part("parse_mode", b"HTML") + part("photo", pngs[0], '; filename="alert.png"')
    else:
        method = "sendMediaGroup"
        media = [{"type": "photo", "media": f"attach://p{k}"} for k in range(len(pngs))]
        media[0].update(caption=caption, parse_mode="HTML")
        body += part("media", json.dumps(media).encode())
        body += b"".join(part(f"p{k}", png, f'; filename="alert{k}.png"') for k, png in enumerate(pngs))
    body += f"--{boundary}--\r\n".encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", body,
                                     {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=60) as r:
        if not json.load(r).get("ok"):
            raise RuntimeError("Telegram refused the photo")


def _telegram(s: Summary, url: str | None, token: str, chat_id: str) -> str:
    """The card image without notes, with a caption holding the links, then the card with each stock's note
    as an album (when any stock has one); without an image, the text card followed by the notes as text."""
    png = card_png(s, notes=False)
    if png:
        telegram_photo(png, caption_html(s, url), token, chat_id)
        notes = sum(b.symbol in s.notes for b in s.mentioned())
        album = card_png(s) if notes else None
        if album:
            telegram_photo(album, f"🤖 <b>Notes · {_day(s.date)}</b>", token, chat_id)
        return "sent (card image" + (f" + {notes} notes album)" if album else ")")
    telegram(telegram_html(s, url), token, chat_id)
    sent = "sent (text)"
    for text in notes_html(f"Notes · {_day(s.date)}", stock_notes(s)):
        telegram(text, token, chat_id)
    return sent + (f" + {len(stock_notes(s))} notes" if stock_notes(s) else "")


def _telegram_commodities(c: Commodities, url: str | None, token: str, chat_id: str) -> str:
    """The commodity card, which carries each contract's note, as its own message after the stocks one; without
    an image, the text card followed by the notes as text."""
    png = commodity_png(c)
    if png:
        telegram_photo(png, commodity_caption(c, url), token, chat_id)
        notes = sum(x.symbol in c.notes for x in c.contracts)
        return "sent (card image" + (f", {notes} notes)" if notes else ")")
    telegram(commodity_text(c, url), token, chat_id)
    for text in notes_html(f"Commodity notes · {_day(c.date)}", commodity_notes(c)):
        telegram(text, token, chat_id)
    return "sent (text)" + (f" + {len(commodity_notes(c))} notes" if commodity_notes(c) else "")


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
         to_email: bool = True, c: Commodities | None = None) -> list[str]:
    """Send to each channel that is enabled and configured (secrets in the environment); returns what
    happened, one line per channel. A failing channel is reported and the others still go out. Commodities
    (`c`) go to Telegram as a second message."""
    env = os.environ.get
    channels = []
    if to_telegram:
        channels.append(("Telegram", ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
                         lambda: _telegram(s, url, env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID"))))
        if c is not None:
            channels.append(("Telegram commodities", ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
                             lambda: _telegram_commodities(c, url, env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID"))))
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
