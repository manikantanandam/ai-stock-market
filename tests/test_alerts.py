import pytest

from aistock import alerts


def journal(tmp_path):
    days = tmp_path / "journal" / "days"
    days.mkdir(parents=True)
    (days / "2026-11-25.csv").write_text("date,symbol,action,combined\n"
                                         "2026-11-25,A,BUY,0.5\n2026-11-25,B,BUY,0.4\n2026-11-25,C,WATCH,0.1\n")
    (days / "2026-11-26.csv").write_text("date,symbol,action,combined\n"
                                         "2026-11-26,A,BUY,0.5\n2026-11-26,B,WATCH,0.2\n2026-11-26,C,BUY,0.6\n")
    return tmp_path


def test_summary_of_the_latest_session(tmp_path):
    s = alerts.summary(journal(tmp_path))
    assert (s.date, s.prev, s.buys, s.new, s.dropped) == ("2026-11-26", "2026-11-25", ["C", "A"], ["C"], ["B"])


def test_summary_of_the_first_session_has_nothing_to_compare(tmp_path):
    s = alerts.summary(journal(tmp_path), date="2026-11-25")
    assert (s.prev, s.buys, s.new, s.dropped) == (None, ["A", "B"], [], [])
    assert alerts.plain(s).splitlines()[2] == "BUY signals: 2"


def test_summary_rejects_a_date_without_session(tmp_path):
    with pytest.raises(ValueError, match="latest before it is 2026-11-26"):
        alerts.summary(journal(tmp_path), date="2026-11-27")
    assert alerts.summary(tmp_path / "empty") is None


def test_plain_text(tmp_path):
    assert alerts.plain(alerts.summary(journal(tmp_path)), url="https://example.org/").splitlines() == [
        "AI Stock · Thu 26 Nov 2026", "",
        "BUY signals: 2 (1 new · 1 dropped since Wed 25 Nov)",
        "New: C",
        "Dropped: B", "",
        "Dashboard: https://example.org/",
    ]


def test_telegram_html_escapes(tmp_path):
    s = alerts.summary(journal(tmp_path))
    s.new = ["M&M"]
    text = alerts.telegram_html(s, url="https://example.org/?a=1&b=2")
    assert text.startswith("📈 <b>AI Stock · Thu 26 Nov 2026</b>")
    assert "🆕 M&amp;M" in text
    assert '<a href="https://example.org/?a=1&amp;b=2">Open dashboard →</a>' in text


def test_markdown(tmp_path):
    text = alerts.markdown(alerts.summary(journal(tmp_path)), url="https://example.org/")
    assert "**BUY signals: 2 (1 new · 1 dropped since Wed 25 Nov)**" in text
    assert "- ➖ B" in text and text.endswith("[Open dashboard →](https://example.org/)")


def test_long_lists_are_capped():
    assert alerts._names([str(i) for i in range(11)]) == "0, 1, 2, 3, 4, 5, 6, 7 +3 more"


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
