import pytest

from aistock import alerts


def journal(tmp_path):
    days = tmp_path / "journal" / "days"
    days.mkdir(parents=True)
    (days / "2026-11-25.csv").write_text("date,symbol,action,combined,close\n"
                                         "2026-11-25,A,BUY,0.5,100\n2026-11-25,B,BUY,0.4,100\n2026-11-25,C,WATCH,0.1,100\n")
    (days / "2026-11-26.csv").write_text("date,symbol,action,combined,close\n"
                                         "2026-11-26,A,BUY,0.5,110\n2026-11-26,B,WATCH,0.2,100\n2026-11-26,C,BUY,0.6,90\n")
    acct = tmp_path / "journal" / "paper" / "trend"
    acct.mkdir(parents=True)
    (acct / "account.json").write_text('{"capital": 100000, "max_positions": 10}')
    (acct / "equity.csv").write_text("date,cash,invested,equity\n2026-11-25,100000,0,100000\n2026-11-26,50000,52000,102000\n")
    (acct / "positions.csv").write_text("entry_date,sym,shares\n2026-11-26,A,10\n2026-11-25,C,5\n")
    (acct / "trades.csv").write_text("symbol,entry_date,exit_date,entry,exit,bars,reason,pnl,ret\n"
                                     "B,2026-11-20,2026-11-26,100,90,4,stop,-500,-0.1\n")
    return tmp_path


def test_summary_of_the_latest_session(tmp_path):
    s = alerts.summary(journal(tmp_path))

    assert (s.date, s.prev, s.buys, s.new, s.dropped) == ("2026-11-26", "2026-11-25", ["C", "A"], ["C"], ["B"])
    (a,) = s.accounts
    # hold: A +10%, B 0%, C -10% -> 0.0%
    assert (a.name, a.ret, a.hold, a.open, a.entered, a.exits) == ("trend", pytest.approx(0.02), pytest.approx(0.0),
                                                                   2, ["A"], [("B", "stop", -0.1)])


def test_summary_of_an_earlier_session_uses_that_days_state(tmp_path):
    s = alerts.summary(journal(tmp_path), date="2026-11-25")

    assert (s.prev, s.buys, s.new) == (None, ["A", "B"], [])
    (a,) = s.accounts
    # C entered 11-25 and is still open; B (traded 11-20 to 11-26) was open too; A came later
    assert (a.ret, a.open, a.entered, a.exits) == (0.0, 2, ["C"], [])


def test_summary_rejects_a_date_without_session(tmp_path):
    with pytest.raises(ValueError, match="latest before it is 2026-11-26"):
        alerts.summary(journal(tmp_path), date="2026-11-27")
    assert alerts.summary(tmp_path / "empty") is None


def test_plain_text(tmp_path):
    text = alerts.plain(alerts.summary(journal(tmp_path)), url="https://example.org/")
    assert text.splitlines() == [
        "AI Stock · Thu 26 Nov 2026", "",
        "BUY signals: 2 (1 new · 1 dropped since Wed 25 Nov)",
        "New: C",
        "Dropped: B", "",
        "Paper accounts (return vs equal-weight hold since start)",
        "      Return   Hold Open",
        "trend  +2.0%  +0.0%    2", "",
        "Today's trades",
        "Bought: trend A",
        "Sold: B (trend, stop −10.0%)", "",
        "Dashboard: https://example.org/",
    ]


def test_telegram_html_escapes_and_keeps_the_table_monospace(tmp_path):
    s = alerts.summary(journal(tmp_path))
    s.new = ["M&M"]
    text = alerts.telegram_html(s, url="https://example.org/?a=1&b=2")
    assert "🆕 M&amp;M" in text
    assert "<pre>      Return   Hold Open\ntrend  +2.0%  +0.0%    2</pre>" in text
    assert '<a href="https://example.org/?a=1&amp;b=2">Open dashboard →</a>' in text


def test_markdown_table(tmp_path):
    text = alerts.markdown(alerts.summary(journal(tmp_path)))
    assert "| trend | +2.0% | +0.0% | 2 |" in text
    assert "- 🔴 **Sold** B (trend, stop −10.0%)" in text


def test_many_entries_are_counted_not_named(tmp_path):
    s = alerts.summary(journal(tmp_path))
    s.accounts[0].entered = ["A", "B", "C"]
    assert alerts._trades(s)[0] == "trend 3 stocks"


def test_send_skips_channels_without_secrets(tmp_path, monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SMTP_USERNAME", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert alerts.send(alerts.summary(journal(tmp_path)), to_issue=False) == [
        "Telegram: skipped (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)",
        "Email: skipped (SMTP_USERNAME / SMTP_PASSWORD not set)"]


def test_a_failing_channel_does_not_stop_the_others(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("SMTP_USERNAME", "me@example.org")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.delenv("ALERT_EMAIL_TO", raising=False)

    def refuse(*a, **k):
        raise RuntimeError("Telegram refused the message")

    sent = []
    monkeypatch.setattr(alerts, "telegram", refuse)
    monkeypatch.setattr(alerts, "email", lambda text, user, pw, to, server, port: sent.append((text.splitlines()[0], to)))
    assert alerts.send(alerts.summary(journal(tmp_path)), to_issue=False) == [
        "Telegram: failed (RuntimeError: Telegram refused the message)", "Email: sent to me@example.org"]
    assert sent == [("AI Stock · Thu 26 Nov 2026", "me@example.org")]
