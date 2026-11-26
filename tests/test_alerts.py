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


def test_send_skips_telegram_without_secrets(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert alerts.send("hi", to_telegram=True, to_issue=False) == [
        "Telegram: skipped (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)"]
