"""Daily alert after the scan: a short plain-text summary of the latest session, sent to Telegram and as a
comment on a "Daily alerts" GitHub issue (`aistock alert`, run at the end of the Daily scan workflow).

The text is built from the journal only (no LLM, decision 0001): BUY count with the new and dropped BUYs
against the previous session, then per paper account its return against the equal-weight hold and the
positions it entered and exited that session. Lines start with "- " so they read the same as plain text
in Telegram and as a list in GitHub markdown.
"""
import json
import os
import subprocess
import urllib.parse
import urllib.request
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
    return pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame()


def message(root: Path = ROOT, url: str | None = None) -> str:
    """The alert for the latest journaled session; empty if there is none."""
    days = sorted((root / "journal" / "days").glob("*.csv"))
    if not days:
        return ""
    date, today = days[-1].stem, pd.read_csv(days[-1])
    buys = today[today.action == "BUY"].sort_values("combined", ascending=False)
    lines = [f"AI stock · {date}"]
    if len(days) > 1:
        prev = pd.read_csv(days[-2])
        before = set(prev.symbol[prev.action == "BUY"])
        new = [s for s in buys.symbol if s not in before]
        dropped = sorted(before - set(buys.symbol))
        lines.append(f"- BUY {len(buys)}: {len(new)} new, {len(dropped)} dropped since {days[-2].stem}")
        if new:
            lines.append(f"- New: {_names(new)}")
        if dropped:
            lines.append(f"- Dropped: {_names(dropped)}")
    else:
        lines.append(f"- BUY {len(buys)}: {_names(list(buys.symbol))}")

    hold = equal_weight(root)
    accounts = sorted(p for p in (root / "journal" / "paper").glob("*") if (p / "account.json").exists())
    if accounts:
        lines.append("Paper accounts (return · equal-weight hold since start):")
    for path in accounts:
        capital = json.loads((path / "account.json").read_text())["capital"]
        equity, trades, positions = (_read(path / f"{k}.csv") for k in ("equity", "trades", "positions"))
        if equity.empty:
            continue
        start, last = str(equity.date.iloc[0]), equity.iloc[-1]
        held = hold.get(str(last.date), float("nan")) / hold.get(start, float("nan")) - 1
        line = f"- {path.name}: {last.equity / capital - 1:+.1%} · hold {held:+.1%} · {len(positions)} open"
        entered = sorted(positions.sym[positions.entry_date == date]) if not positions.empty else []
        exits = trades[trades.exit_date == date] if not trades.empty else trades
        if entered:
            line += f" · in: {_names(entered)}"
        if len(exits):
            line += " · out: " + ", ".join(f"{t.symbol} {t.reason} {t.ret:+.1%}" for t in exits.itertuples())
        lines.append(line)
    if url:
        lines.append(url)
    return "\n".join(lines)


def telegram(text: str, token: str, chat_id: str) -> None:
    """Send `text` from the bot to the chat (Bot API sendMessage)."""
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=30) as r:
        if not json.load(r).get("ok"):
            raise RuntimeError("Telegram refused the message")


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


def send(text: str, to_telegram: bool = True, to_issue: bool = True) -> list[str]:
    """Send to each channel that is enabled and configured; returns what happened, one line per channel."""
    done = []
    if to_telegram:
        token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        if token and chat:
            telegram(text, token, chat)
            done.append("Telegram: sent")
        else:
            done.append("Telegram: skipped (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)")
    if to_issue:
        done.append(f"GitHub issue: {github_issue(text)}")
    return done
