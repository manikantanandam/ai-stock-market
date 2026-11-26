"""Daily alert: a short plain-text summary of one journaled session, sent to Telegram, by email and as a
comment on a "Daily alerts" GitHub issue (`aistock alert`; run at the end of the Daily scan workflow, and by
hand for any date through the Send alert workflow).

The text is built from the journal only (no LLM, decision 0001): BUY count with the new and dropped BUYs
against the previous session, then per paper account its return against the equal-weight hold and the
positions it entered and exited that session. Past dates are rebuilt from the saved files: positions open
on a date are the open ones entered by then plus the closed trades still open then. Lines start with "- "
so they read the same as plain text in Telegram and email and as a list in GitHub markdown.
"""
import json
import os
import smtplib
import subprocess
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

from aistock.site import equal_weight

ROOT = Path(__file__).resolve().parents[2]
ISSUE_TITLE = "Daily alerts"
MAX_NAMES = 15  # symbols listed per line before "+N more"


def _names(symbols: list[str]) -> str:
    more = len(symbols) - MAX_NAMES
    return ", ".join(symbols[:MAX_NAMES]) + (f" +{more} more" if more > 0 else "")


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"entry_date": str, "exit_date": str, "date": str}) \
        if path.exists() and path.stat().st_size else pd.DataFrame()


def sessions(root: Path = ROOT) -> list[str]:
    return [p.stem for p in sorted((root / "journal" / "days").glob("*.csv"))]


def message(root: Path = ROOT, url: str | None = None, date: str | None = None) -> str:
    """The alert for session `date` (default: the latest journaled one); empty if there is no session.
    Raises ValueError for a date with no journaled session."""
    days = sessions(root)
    if not days:
        return ""
    date = date or days[-1]
    if date not in days:
        before = [d for d in days if d < date]
        raise ValueError(f"no session on {date}" + (f"; the latest before it is {before[-1]}" if before else ""))
    i = days.index(date)
    today = pd.read_csv(root / "journal" / "days" / f"{date}.csv")
    buys = today[today.action == "BUY"].sort_values("combined", ascending=False)
    lines = [f"AI stock · {date}"]
    if i > 0:
        prev = pd.read_csv(root / "journal" / "days" / f"{days[i - 1]}.csv")
        before = set(prev.symbol[prev.action == "BUY"])
        new = [s for s in buys.symbol if s not in before]
        dropped = sorted(before - set(buys.symbol))
        lines.append(f"- BUY {len(buys)}: {len(new)} new, {len(dropped)} dropped since {days[i - 1]}")
        if new:
            lines.append(f"- New: {_names(new)}")
        if dropped:
            lines.append(f"- Dropped: {_names(dropped)}")
    else:
        lines.append(f"- BUY {len(buys)}: {_names(list(buys.symbol))}")

    hold = equal_weight(root)
    accounts = sorted(p for p in (root / "journal" / "paper").glob("*") if (p / "account.json").exists())
    rows = []
    for path in accounts:
        capital = json.loads((path / "account.json").read_text())["capital"]
        equity, trades, positions = (_read(path / f"{k}.csv") for k in ("equity", "trades", "positions"))
        if equity.empty or date not in set(equity.date):
            continue
        start, eq = equity.date.iloc[0], equity.loc[equity.date == date, "equity"].iloc[-1]
        held = hold.get(date, float("nan")) / hold.get(start, float("nan")) - 1
        opened = (list(positions.sym[positions.entry_date <= date]) if not positions.empty else []) + \
            (list(trades.symbol[(trades.entry_date <= date) & (trades.exit_date > date)]) if not trades.empty else [])
        entered = sorted((set(positions.sym[positions.entry_date == date]) if not positions.empty else set())
                         | (set(trades.symbol[trades.entry_date == date]) if not trades.empty else set()))
        exits = trades[trades.exit_date == date] if not trades.empty else trades
        line = f"- {path.name}: {eq / capital - 1:+.1%} · hold {held:+.1%} · {len(opened)} open"
        if entered:
            line += f" · in: {_names(entered)}"
        if len(exits):
            line += " · out: " + ", ".join(f"{t.symbol} {t.reason} {t.ret:+.1%}" for t in exits.itertuples())
        rows.append(line)
    if rows:
        lines += ["Paper accounts (return · equal-weight hold since start):", *rows]
    if url:
        lines.append(url)
    return "\n".join(lines)


def telegram(text: str, token: str, chat_id: str) -> None:
    """Send `text` from the bot to the chat (Bot API sendMessage)."""
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
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


def send(text: str, to_telegram: bool = True, to_issue: bool = True, to_email: bool = True) -> list[str]:
    """Send to each channel that is enabled and configured (secrets in the environment); returns what
    happened, one line per channel. A failing channel is reported and the others still go out."""
    env = os.environ.get
    channels = []
    if to_telegram:
        channels.append(("Telegram", ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
                         lambda: telegram(text, env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")) or "sent"))
    if to_email:
        channels.append(("Email", ("SMTP_USERNAME", "SMTP_PASSWORD"),
                         lambda: email(text, env("SMTP_USERNAME"), env("SMTP_PASSWORD"),
                                       env("ALERT_EMAIL_TO") or env("SMTP_USERNAME"),
                                       env("SMTP_SERVER") or "smtp.gmail.com", int(env("SMTP_PORT") or 465))
                         or f"sent to {env('ALERT_EMAIL_TO') or env('SMTP_USERNAME')}"))
    if to_issue:
        channels.append(("GitHub issue", (), lambda: github_issue(text)))
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
