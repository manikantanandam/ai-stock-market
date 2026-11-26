"""Daily alert: a short summary of one journaled session, sent to Telegram, by email and as a comment on a
"Daily alerts" GitHub issue (`aistock alert`; run at the end of the Daily scan workflow, and by hand for any
date through the Send alert workflow).

`summary` reads the session's journal day file only (no LLM, decision 0001): BUY count with the new and
dropped BUYs against the previous session. Paper accounts are left to the dashboard. Each channel gets its own
rendering of the same summary: `telegram_html` (bold titles), `markdown` (the issue) and `plain` (email and the
terminal).
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

ROOT = Path(__file__).resolve().parents[2]
ISSUE_TITLE = "Daily alerts"
MAX_NAMES = 8  # symbols listed before "+N more"


@dataclass
class Summary:
    date: str
    prev: str | None
    buys: list[str]  # best combined score first
    new: list[str]
    dropped: list[str]


def _names(symbols: list[str], limit: int = MAX_NAMES) -> str:
    more = len(symbols) - limit
    return ", ".join(symbols[:limit]) + (f" +{more} more" if more > 0 else "")


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
    return Summary(date, prev, buys, new, dropped)


# ---------- rendering: one summary, three formats ----------

def _day(d: str) -> str:
    return datetime.strptime(d, "%Y-%m-%d").strftime("%a %d %b %Y")


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
    if url:
        lines += ["", f"Dashboard: {url}"]
    return "\n".join(lines)


def telegram_html(s: Summary, url: str | None = None) -> str:
    """Telegram HTML: bold title and BUY line, emoji markers for new and dropped BUYs."""
    e = html.escape
    lines = [f"📈 <b>AI Stock · {_day(s.date)}</b>", "", f"<b>{e(_buy_line(s))}</b>"]
    if s.new:
        lines.append(f"🆕 {e(_names(s.new))}")
    if s.dropped:
        lines.append(f"➖ {e(_names(s.dropped))}")
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
