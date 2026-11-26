import pytest

from aistock import alerts

COLS = "date,symbol,name,sector,action,buys,combined\n"


def journal(tmp_path):
    days = tmp_path / "journal" / "days"
    days.mkdir(parents=True)
    (days / "2026-11-25.csv").write_text(COLS + "2026-11-25,A,Alpha Limited,Tech,BUY,swing,0.5\n"
                                                "2026-11-25,B,Beta Ltd.,Energy,BUY,trend,0.4\n"
                                                "2026-11-25,C,Gamma,Tech,WATCH,,0.1\n")
    (days / "2026-11-26.csv").write_text(COLS + "2026-11-26,A,Alpha Limited,Tech,BUY,swing,0.5\n"
                                                "2026-11-26,B,Beta Ltd.,Energy,WATCH,,0.2\n"
                                                "2026-11-26,C,Gamma,Tech,BUY,swing;trend;breakout,0.6\n")
    return tmp_path


def test_summary_of_the_latest_session(tmp_path):
    s = alerts.summary(journal(tmp_path))
    assert (s.date, s.prev, s.dropped) == ("2026-11-26", "2026-11-25", ["B"])
    assert [b.symbol for b in s.buys] == ["C", "A"]  # best score first
    assert s.new == [alerts.Stock("C", "Gamma", "Tech", 0.6, ["swing", "trend", "breakout"])]
    assert s.buys[1].name == "Alpha"  # legal suffix dropped


def test_summary_of_the_first_session_has_nothing_to_compare(tmp_path):
    s = alerts.summary(journal(tmp_path), date="2026-11-25")
    assert (s.prev, [b.symbol for b in s.buys], s.new, s.dropped) == (None, ["A", "B"], [], [])
    assert alerts.plain(s).splitlines()[1] == "2 BUY · 1 WATCH · 0 AVOID (67% of 3 stocks say BUY)"


def test_summary_rejects_a_date_without_session(tmp_path):
    with pytest.raises(ValueError, match="latest before it is 2026-11-26"):
        alerts.summary(journal(tmp_path), date="2026-11-27")
    assert alerts.summary(tmp_path / "empty") is None


def test_plain_text(tmp_path):
    assert alerts.plain(alerts.summary(journal(tmp_path)), url="https://example.org/").splitlines() == [
        "AI Stock · Thu 26 Nov 2026",
        "2 BUY · 1 WATCH · 0 AVOID (67% of 3 stocks say BUY)",
        "Hot sectors: Tech 2", "",
        "New BUYs (1, 1 dropped since Wed 25 Nov)",
        "  C · Gamma · Tech · score +0.60 · 3 strategies", "",
        "Top 2 by score",
        "  C  +0.60  Tech",
        "  A  +0.50  Tech", "",
        "Dropped: B", "",
        "Dashboard: https://example.org/",
    ]


def test_telegram_card(tmp_path):
    s = alerts.summary(journal(tmp_path))
    s.new[0].name = "M&M"
    lines = alerts.telegram_html(s, url="https://example.org/").splitlines()
    assert lines[:9] == ["📈 <b>AI STOCK DAILY</b>", "<i>Thu 26 Nov 2026 · NSE close</i>", alerts.RULE,
                         "🟢 <b>2</b> BUY   🟡 <b>1</b> WATCH   🔴 <b>0</b> AVOID",
                         "<code>▰▰▰▰▰▰▰▱▱▱</code> 67% of 3 stocks say BUY", "",
                         "🔥 <b>HOT SECTORS</b> <i>(BUYs)</i>", "▫️ <code>  2</code> Tech", ""]  # unknown sector: plain marker
    text = "\n".join(lines)
    # company names link to their dashboard page (escaped), so they show in the link colour
    link = '<a href="https://example.org/#journal/stocks/C.csv">M&amp;M</a>'
    # scores lead each line (fixed width), so they form a column
    assert (f"🆕 <b>NEW BUYS</b> · 1  <i>(➖ 1 dropped)</i>\n▫️ <code>+0.60</code> <b>C</b>\n"
            f"{alerts.INDENT}{link} · <i>3 strategies</i>") in text
    assert f"🥇 <code>+0.60</code> <b>C</b> · {link}\n🥈 <code>+0.50</code> <b>A</b>" in text
    assert "<blockquote expandable>➖ <b>No longer BUY</b> (1)\nB</blockquote>" in text
    assert lines[-1] == '👉 <a href="https://example.org/"><b>Open dashboard</b></a>'


def test_company_without_a_dashboard_is_italic():
    stock = alerts.Stock("M&M", "Mahindra & Mahindra", "Consumer Cyclical", 0.5, [])
    assert alerts._company(stock, None) == "<i>Mahindra &amp; Mahindra</i>"
    assert 'stocks/M%26M.csv">' in alerts._company(stock, "https://x/")


def test_bar():
    assert (alerts._bar(0.21, 10), alerts._bar(1.5, 4), alerts._bar(-1, 3)) == ("▰▰▱▱▱▱▱▱▱▱", "▰▰▰▰", "▱▱▱")


def test_markdown_tables(tmp_path):
    text = alerts.markdown(alerts.summary(journal(tmp_path)), url="https://example.org/")
    assert text.splitlines()[1:3] == ["🟢 **2** BUY · 🟡 1 WATCH · 🔴 0 AVOID", "🔥 Tech 2"]
    assert "| **C** | Gamma | Tech | +0.60 | 3 strategies |" in text
    assert "| **A** | +0.50 | Tech |" in text
    assert text.endswith("[Open dashboard →](https://example.org/)")


def test_long_lists_are_capped(tmp_path):
    assert alerts._names([str(i) for i in range(11)], 8) == "0, 1, 2, 3, 4, 5, 6, 7 +3 more"
    s = alerts.summary(journal(tmp_path))
    s.new = s.new * 10
    assert "+2 more on the dashboard" in alerts.telegram_html(s)


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
