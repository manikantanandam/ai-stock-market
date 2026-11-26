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


def test_message_lists_new_and_dropped_buys_and_each_account(tmp_path):
    text = alerts.message(journal(tmp_path), url="https://example.org/")

    assert text.splitlines() == [
        "AI stock · 2026-11-26",
        "- BUY 2: 1 new, 1 dropped since 2026-11-25",
        "- New: C",
        "- Dropped: B",
        "Paper accounts (return · equal-weight hold since start):",
        # hold: A +10%, B 0%, C -10% -> 0.0%
        "- trend: +2.0% · hold +0.0% · 2 open · in: A · out: B stop -10.0%",
        "https://example.org/",
    ]


def test_message_is_empty_without_sessions(tmp_path):
    assert alerts.message(tmp_path) == ""


def test_names_cap_long_lists():
    assert alerts._names([str(i) for i in range(18)]).endswith("14 +3 more")


def test_message_for_an_earlier_session_uses_that_days_state(tmp_path):
    text = alerts.message(journal(tmp_path), date="2026-11-25")

    assert text.splitlines() == [
        "AI stock · 2026-11-25",
        "- BUY 2: A, B",
        "Paper accounts (return · equal-weight hold since start):",
        # C entered 11-25 and is still open; B (traded 11-20 to 11-26) was open too; A came later
        "- trend: +0.0% · hold +0.0% · 2 open · in: C",
    ]


def test_message_rejects_a_date_without_session(tmp_path):
    with pytest.raises(ValueError, match="latest before it is 2026-11-26"):
        alerts.message(journal(tmp_path), date="2026-11-27")


def test_send_skips_channels_without_secrets(monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SMTP_USERNAME", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert alerts.send("hi", to_telegram=True, to_issue=False, to_email=True) == [
        "Telegram: skipped (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)",
        "Email: skipped (SMTP_USERNAME / SMTP_PASSWORD not set)"]


def test_a_failing_channel_does_not_stop_the_others(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("SMTP_USERNAME", "me@example.org")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.delenv("ALERT_EMAIL_TO", raising=False)

    def refuse(*a, **k):
        raise RuntimeError("Telegram refused the message")

    sent = []
    monkeypatch.setattr(alerts, "telegram", refuse)
    monkeypatch.setattr(alerts, "email", lambda text, user, pw, to, server, port: sent.append((to, server, port)))
    assert alerts.send("hi", to_issue=False) == [
        "Telegram: failed (RuntimeError: Telegram refused the message)", "Email: sent to me@example.org"]
    assert sent == [("me@example.org", "smtp.gmail.com", 465)]
