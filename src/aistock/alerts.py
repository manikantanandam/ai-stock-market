"""Daily alert: a short summary of one journaled session, sent to Telegram, by email and as a comment on a
"Daily alerts" GitHub issue (`aistock alert`; run at the end of the Daily scan workflow, and by hand for any
date through the Send alert workflow).

`summary` reads the journal only (no LLM, decision 0001): BUY count with the new and dropped BUYs against the
previous session, then per paper account its return against the equal-weight hold, open positions and that
session's entries and exits. Past dates are rebuilt from the saved files: positions open on a date are the
open ones entered by then plus the closed trades still open then. Each channel gets its own rendering of the
same summary: `telegram_html` (bold titles, the accounts table in a monospace block), `markdown` (the issue)
and `plain` (email and the terminal).
"""
import html
import json
import os
import smtplib
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

from aistock.site import equal_weight

ROOT = Path(__file__).resolve().parents[2]
ISSUE_TITLE = "Daily alerts"
MAX_NAMES = 8  # symbols listed before "+N more"
NAMED_ENTRIES = 2  # an account's entries are named up to this many, else counted


@dataclass
class Account:
    name: str
    ret: float    # since the account started
    hold: float   # equal-weight hold over the same dates
    open: int
    entered: list[str]
    exits: list[tuple[str, str, float]]  # (symbol, reason, return)


@dataclass
class Summary:
    date: str
    prev: str | None
    buys: list[str]  # best combined score first
    new: list[str]
    dropped: list[str]
    accounts: list[Account]  # best return first


def _names(symbols: list[str], limit: int = MAX_NAMES) -> str:
    more = len(symbols) - limit
    return ", ".join(symbols[:limit]) + (f" +{more} more" if more > 0 else "")


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"entry_date": str, "exit_date": str, "date": str}) \
        if path.exists() and path.stat().st_size else pd.DataFrame()


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
    buys = list(today[today.action == "BUY"].sort_values("combined", ascending=False).symbol)
    prev, new, dropped = None, [], []
    if i > 0:
        prev = days[i - 1]
        before = pd.read_csv(root / "journal" / "days" / f"{prev}.csv")
        was = set(before.symbol[before.action == "BUY"])
        new, dropped = [s for s in buys if s not in was], sorted(was - set(buys))

    hold = equal_weight(root)
    accounts = []
    for path in sorted(p for p in (root / "journal" / "paper").glob("*") if (p / "account.json").exists()):
        capital = json.loads((path / "account.json").read_text())["capital"]
        equity, trades, positions = (_read(path / f"{k}.csv") for k in ("equity", "trades", "positions"))
        if equity.empty or date not in set(equity.date):
            continue
        start, eq = equity.date.iloc[0], equity.loc[equity.date == date, "equity"].iloc[-1]
        has_pos, has_trades = not positions.empty, not trades.empty
        opened = (list(positions.sym[positions.entry_date <= date]) if has_pos else []) + \
            (list(trades.symbol[(trades.entry_date <= date) & (trades.exit_date > date)]) if has_trades else [])
        entered = sorted((set(positions.sym[positions.entry_date == date]) if has_pos else set())
                         | (set(trades.symbol[trades.entry_date == date]) if has_trades else set()))
        exits = [(t.symbol, t.reason, t.ret) for t in trades[trades.exit_date == date].itertuples()] if has_trades else []
        accounts.append(Account(path.name, eq / capital - 1, hold.get(date, float("nan")) / hold.get(start, float("nan")) - 1,
                                len(opened), entered, exits))
    accounts.sort(key=lambda a: a.ret, reverse=True)
    return Summary(date, prev, buys, new, dropped, accounts)


# ---------- rendering: one summary, three formats ----------

def _day(d: str) -> str:
    return datetime.strptime(d, "%Y-%m-%d").strftime("%a %d %b %Y")


def _pct(x: float) -> str:
    return f"{x:+.1%}".replace("-", "−")  # a real minus sign lines up with "+"


def _table(s: Summary) -> list[str]:
    """Accounts as fixed-width rows for monospace blocks, ~32 characters so they don't wrap on a phone."""
    width = max(len(a.name) for a in s.accounts)
    rows = [f"{'':<{width}} {'Return':>6} {'Hold':>6} {'Open':>4}"]
    rows += [f"{a.name:<{width}} {_pct(a.ret):>6} {_pct(a.hold):>6} {a.open:>4}" for a in s.accounts]
    return rows


def _trades(s: Summary) -> tuple[str, str]:
    """(bought, sold) lines: per account its entries (named when few, counted when many) and every exit."""
    bought = [f"{a.name} {', '.join(a.entered)}" if len(a.entered) <= NAMED_ENTRIES else f"{a.name} {len(a.entered)} stocks"
              for a in s.accounts if a.entered]
    sold = [f"{sym} ({a.name}, {reason} {_pct(ret)})" for a in s.accounts for sym, reason, ret in a.exits]
    return " · ".join(bought), " · ".join(sold)


def _buy_line(s: Summary) -> str:
    if s.prev is None:
        return f"BUY signals: {len(s.buys)}"
    return f"BUY signals: {len(s.buys)} ({len(s.new)} new · {len(s.dropped)} dropped since {_day(s.prev)[:-5]})"


def plain(s: Summary, url: str | None = None) -> str:
    """Plain text: email and the terminal."""
    lines = [f"AI Stock · {_day(s.date)}", "", _buy_line(s)]
    if s.new:
        lines.append(f"New: {_names(s.new)}")
    if s.dropped:
        lines.append(f"Dropped: {_names(s.dropped)}")
    if s.accounts:
        bought, sold = _trades(s)
        lines += ["", "Paper accounts (return vs equal-weight hold since start)", *_table(s)]
        if bought or sold:
            lines += ["", "Today's trades"] + ([f"Bought: {bought}"] if bought else []) + ([f"Sold: {sold}"] if sold else [])
    if url:
        lines += ["", f"Dashboard: {url}"]
    return "\n".join(lines)


def telegram_html(s: Summary, url: str | None = None) -> str:
    """Telegram HTML: bold titles, the accounts table in a monospace block."""
    e = html.escape
    lines = [f"📈 <b>AI Stock · {_day(s.date)}</b>", "", f"<b>{e(_buy_line(s))}</b>"]
    if s.new:
        lines.append(f"🆕 {e(_names(s.new))}")
    if s.dropped:
        lines.append(f"➖ {e(_names(s.dropped))}")
    if s.accounts:
        bought, sold = _trades(s)
        lines += ["", "💼 <b>Paper accounts</b> <i>(vs equal-weight hold)</i>", "<pre>" + e("\n".join(_table(s))) + "</pre>"]
        if bought:
            lines.append(f"🟢 <b>Bought</b> {e(bought)}")
        if sold:
            lines.append(f"🔴 <b>Sold</b> {e(sold)}")
    if url:
        lines += ["", f'<a href="{e(url)}">Open dashboard →</a>']
    return "\n".join(lines)


def markdown(s: Summary, url: str | None = None) -> str:
    """GitHub markdown: the issue comment."""
    lines = [f"### AI Stock · {_day(s.date)}", "", f"**{_buy_line(s)}**", ""]
    if s.new:
        lines.append(f"- 🆕 {_names(s.new)}")
    if s.dropped:
        lines.append(f"- ➖ {_names(s.dropped)}")
    if s.accounts:
        bought, sold = _trades(s)
        lines += ["", "| Paper account | Return | Hold | Open |", "|---|---:|---:|---:|"]
        lines += [f"| {a.name} | {_pct(a.ret)} | {_pct(a.hold)} | {a.open} |" for a in s.accounts]
        if bought or sold:
            lines += [""] + ([f"- 🟢 **Bought** {bought}"] if bought else []) + ([f"- 🔴 **Sold** {sold}"] if sold else [])
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
                         lambda: telegram(telegram_html(s, url), env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")) or "sent"))
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
