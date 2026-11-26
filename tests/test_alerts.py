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
    assert alerts.plain(s).splitlines()[1] == "2 BUY signals"


def test_summary_rejects_a_date_without_session(tmp_path):
    with pytest.raises(ValueError, match="latest before it is 2026-11-26"):
        alerts.summary(journal(tmp_path), date="2026-11-27")
    assert alerts.summary(tmp_path / "empty") is None


def test_plain_text(tmp_path):
    assert alerts.plain(alerts.summary(journal(tmp_path)), url="https://example.org/").splitlines() == [
        "AI Stock · Thu 26 Nov 2026",
        "2 BUY signals · 1 new · 1 dropped", "",
        "New BUYs",
        "  C · Gamma · Tech · score +0.60 · 3 strategies", "",
        "Top 2 by score",
        "  C  +0.60  Tech",
        "  A  +0.50  Tech", "",
        "Dropped: B", "",
        "Dashboard: https://example.org/",
    ]


def test_telegram_html_escapes_and_formats_each_new_buy(tmp_path):
    s = alerts.summary(journal(tmp_path))
    s.new[0].name = "M&M"
    text = alerts.telegram_html(s, url="https://example.org/?a=1&b=2")
    assert text.startswith("📈 <b>AI Stock</b> · Thu 26 Nov 2026")
    assert "<b>C</b> · M&amp;M\n    Tech · <code>+0.60</code> · <i>3 strategies</i>" in text
    assert "<code>A +0.50</code>  Tech" in text
    assert '👉 <a href="https://example.org/?a=1&amp;b=2">Open dashboard</a>' in text


def test_markdown_tables(tmp_path):
    text = alerts.markdown(alerts.summary(journal(tmp_path)), url="https://example.org/")
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
