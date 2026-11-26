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
    assert (s.prev_share, s.prev_sectors) == (pytest.approx(2 / 3), {"Tech": 1, "Energy": 1})
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
        "2 BUY · 1 WATCH · 0 AVOID (67% of 3 stocks say BUY, = on the previous session)",
        "Hot sectors: Tech 2", "",
        "New BUYs (1)",
        "  [swing, trend, breakout] (1)",
        "    C · Gamma · Tech · score +0.60", "",
        "Top 2 by score",
        "  C  +0.60  Tech",
        "  A  +0.50  Tech", "",
        "Dropped: B", "",
        "Dashboard: https://example.org/",
    ]


def test_telegram_card(tmp_path):
    s = alerts.summary(journal(tmp_path))
    s.new[0].symbol = s.buys[0].symbol = "M&M"
    lines = alerts.telegram_html(s, url="https://example.org/").splitlines()
    assert lines[:9] == ["📈 <b>AI STOCK DAILY</b>", "<i>Thu 26 Nov 2026 · NSE close</i>", alerts.RULE,
                         "🟢 <b>2</b> BUY   🟡 <b>1</b> WATCH   🔴 <b>0</b> AVOID",
                         "<code>▰▰▰▰▰▰▰▱▱▱</code> 67% say BUY <i>(=)</i>", "",
                         "🔥 <b>HOT SECTORS</b> <i>(BUYs)</i>", "▫️ <code>  2</code> Tech <i>(▲1)</i>", ""]
    text = "\n".join(lines)
    # symbols link to their dashboard page (escaped), so they show in the link colour; scores lead, so they align
    link = '<b><a href="https://example.org/#journal/stocks/M%26M.csv">M&amp;M</a></b>'
    assert ("🆕 <b>NEW BUYS</b> · 1\n🔹 <b>swing · trend · breakout</b> <i>(1)</i>\n"
            f"▫️ <code>+0.60</code> {link} · <i>Gamma</i>\n") in text
    assert f"🥇 <code>+0.60</code> {link} 🆕 · <i>Gamma</i>\n🥈 <code>+0.50</code> <b><a " in text
    assert "<blockquote expandable>➖ <b>No longer BUY</b> (1)\nB</blockquote>" in text
    assert lines[-1] == ('👉 <a href="https://example.org/#journal/days/2026-11-26.csv"><b>Open this session</b></a>'
                         ' · <a href="https://example.org/">Dashboard</a>')


def test_new_buys_are_grouped_by_strategy_set_broadest_first():
    stock = lambda sym, strategies: alerts.Stock(sym, sym, "Tech", 0.5, strategies)
    a, b, c, d = stock("A", ["swing", "trend"]), stock("B", ["swing", "trend", "leaders"]), \
        stock("C", ["swing", "trend"]), stock("D", ["breakout"])
    assert alerts._groups([a, b, c, d]) == [(["swing", "trend", "leaders"], [b]), (["swing", "trend"], [a, c]),
                                            (["breakout"], [d])]


def test_change():
    assert [alerts._change(5, 2), alerts._change(1, 4), alerts._change(3, 3), alerts._change(3, None)] == \
        ["(▲3)", "(▼3)", "(=)", "(new)"]
    assert alerts._change(0.21, 0.22, pct=True) == "(▼1%)"


def test_symbol_without_a_dashboard_is_just_bold():
    assert alerts._symbol(alerts.Stock("M&M", "Mahindra", "Consumer Cyclical", 0.5, []), None) == "<b>M&amp;M</b>"


def test_bar():
    assert (alerts._bar(0.21, 10), alerts._bar(1.5, 4), alerts._bar(-1, 3)) == ("▰▰▱▱▱▱▱▱▱▱", "▰▰▰▰", "▱▱▱")


def test_markdown_tables(tmp_path):
    text = alerts.markdown(alerts.summary(journal(tmp_path)), url="https://example.org/")
    assert text.splitlines()[1:3] == ["🟢 **2** BUY · 🟡 1 WATCH · 🔴 0 AVOID", "🔥 Tech 2"]
    assert "| **C** | Gamma | Tech | +0.60 | swing, trend, breakout |" in text
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
