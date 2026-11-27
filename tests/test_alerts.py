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
    assert lines[:10] == ["📈 <b>AI STOCK DAILY</b>", "<i>Thu 26 Nov 2026 · NSE close</i>", alerts.RULE,
                          "🟢 <b>2</b> BUY   🟡 <b>1</b> WATCH   🔴 <b>0</b> AVOID",
                          "🟩🟩🟩🟩🟩🟩🟩⬜⬜⬜", "<b>67%</b> of 3 stocks say BUY  ⚪ 0%", "",
                          "🔥 <b>HOT SECTORS</b> <i>· BUYs</i>", "▫️ <code>  2</code> Tech  🟢 +1", ""]
    text = "\n".join(lines)
    # symbols link to their dashboard page (escaped), so they show in the link colour; each strategy set is a
    # quote card headed by its strategies, scores first so they align
    link = '<b><a href="https://example.org/#journal/stocks/M%26M.csv">M&amp;M</a></b>'
    assert ("🆕 <b>NEW BUYS</b> · 1\n<blockquote>🔹 <b>swing · trend · breakout</b>  <i>×1</i>\n"
            f"▫️ <code>+0.60</code> {link} · <i>Gamma</i></blockquote>") in text
    assert f"🥇 <code>+0.60</code> {link} 🆕 · <i>Gamma</i>\n🥈 <code>+0.50</code> <b><a " in text
    assert "<blockquote expandable>➖ <b>No longer BUY</b> (1)\nB</blockquote>" in text
    assert lines[-1] == ('👉 <a href="https://example.org/#journal/days/2026-11-26.csv"><b>Open this session</b></a>'
                         '  ·  <a href="https://example.org/">Dashboard</a>')


def test_new_buys_share_one_card_with_a_header_per_strategy_set():
    stock = lambda sym, strategies: alerts.Stock(sym, sym, "Tech", 0.5, strategies)
    summary = alerts.Summary("2026-11-26", "2026-11-25", [], [stock("A", ["swing", "trend"]), stock("B", ["swing"])],
                             [], {"BUY": 2}, [])
    text = alerts.telegram_html(summary)
    assert text.count("<blockquote>") == 1
    assert "🔹 <b>swing · trend</b>  <i>×1</i>\n▫️ <code>+0.50</code> <b>A</b> · <i>A</i>\n\n🔹 <b>swing</b>" in text


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


def test_squares_and_tint():
    assert (alerts._squares(0.21), alerts._squares(1.5, 4), alerts._squares(-1, 3)) == \
        ("🟩🟩⬜⬜⬜⬜⬜⬜⬜⬜", "🟩🟩🟩🟩", "⬜⬜⬜")
    assert [alerts._tint(5, 2), alerts._tint(1, 4), alerts._tint(3, 3), alerts._tint(3, None),
            alerts._tint(0.21, 0.22, pct=True)] == ["🟢 +3", "🔴 −3", "⚪ 0", "✨ new", "🔴 −1%"]


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
        raise RuntimeError("Telegram refused the photo")

    sent = []
    monkeypatch.setattr(alerts, "card_png", lambda s, notes=True: [b"png"])
    monkeypatch.setattr(alerts, "telegram_photo", refuse)
    monkeypatch.setattr(alerts, "email", lambda text, user, pw, to, server, port: sent.append((text.splitlines()[0], to)))
    assert alerts.send(alerts.summary(journal(tmp_path)), to_issue=False) == [
        "Telegram: failed (RuntimeError: Telegram refused the photo)", "Email: sent to me@example.org"]
    assert sent == [("AI Stock · Thu 26 Nov 2026", "me@example.org")]


def test_telegram_sends_the_card_image_with_the_links_in_its_caption(tmp_path, monkeypatch):
    got = {}
    monkeypatch.setattr(alerts, "card_png", lambda s, notes=True: [b"png"])
    monkeypatch.setattr(alerts, "telegram_photo", lambda png, caption, token, chat: got.update(png=png, caption=caption))
    assert alerts._telegram(alerts.summary(journal(tmp_path)), "https://example.org/", "t", "c") == "sent (card image)"
    assert got["png"] == [b"png"]
    assert got["caption"] == ("📈 <b>AI Stock · Thu 26 Nov 2026</b> · 2 BUYs, 1 new\n"
                              "➖ <i>No longer BUY (1): B</i>\n"
                              '👉 <a href="https://example.org/#journal/days/2026-11-26.csv">Open this session</a>'
                              '  ·  <a href="https://example.org/">Dashboard</a>')


def test_telegram_falls_back_to_text_without_an_image(tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(alerts, "card_png", lambda s, notes=True: None)
    monkeypatch.setattr(alerts, "telegram", lambda text, token, chat: got.append(text))
    assert alerts._telegram(alerts.summary(journal(tmp_path)), None, "t", "c") == "sent (text)"
    assert got[0].startswith("📈 <b>AI STOCK DAILY</b>")


def test_card_html_holds_every_section(tmp_path):
    s = alerts.summary(journal(tmp_path))
    s.new[0].name = "M&M"
    page = alerts.card_html(s)
    assert 'of 3 stocks say BUY</span><span class="chip flat">0%</span>' in page
    assert 'style="background:#3b82f6">swing</span>' in page and '<span class="count">×1</span>' in page
    assert '<span class="meta">M&amp;M · <span class="hot">Tech</span></span>' in page  # Tech is a hot sector  # escaped
    assert '<span class="medal m1">1</span>' in page and '<span class="chip new">NEW</span>' in page
    assert "No longer BUY" not in page  # in the caption, so the image stays short
    assert "document.body.dataset.shown" in page  # the renderer waits for it


def test_score_note_follows_the_live_weights(monkeypatch):
    from functools import partial

    from aistock.strategy import signal

    # dataclass defaults are fixed in __init__, so swap in a Config with other ones
    monkeypatch.setattr(signal, "Config", partial(signal.Config, w_technical=0.6, w_fundamental=0.2))
    note = alerts.score_note()
    assert "−1 to +1 = 60% technical + 20% fundamentals + 20% news sentiment" in note
    assert "(swing needs ≥ +0.40)" in note
    assert '<div class="note">' in alerts.card_html(alerts.Summary("2026-11-26", None, [], [], [], {}, []))


def test_sector_is_highlighted_only_when_hot():
    assert alerts._sector("Healthcare", {"Healthcare"}) == '<span class="hot">Healthcare</span>'
    assert alerts._sector("Energy", {"Healthcare"}) == "Energy"


CCOLS = "date,symbol,name,group,unit,action,buys,sells,exits,holds,combined,close,side\n"


def commodities(tmp_path):
    days = tmp_path / "journal" / "commodities" / "days"
    days.mkdir(parents=True)
    (days / "2026-11-24.csv").write_text(CCOLS + "2026-11-24,GOLD,Gold,Bullion,10 g,WATCH,,,,,0.1,100,\n"
                                                 "2026-11-24,CRUDEOIL,Crude oil,Energy,1 bbl,HOLD,,,,trend:long,0.2,50,long\n")
    (days / "2026-11-25.csv").write_text(CCOLS + "2026-11-25,GOLD,Gold,Bullion,10 g,SELL,,channel:short;trend:short,,,-0.4,98,short\n"
                                                 "2026-11-25,CRUDEOIL,Crude oil,Energy,1 bbl,HOLD,,,,trend:long,0.3,51,long\n")
    return tmp_path


def test_commodity_summary_takes_the_latest_session_on_or_before_the_date(tmp_path):
    root = commodities(tmp_path)
    c = alerts.commodity_summary(root, date="2026-11-26")  # stocks traded on a US holiday
    assert (c.date, c.prev) == ("2026-11-25", "2026-11-24")
    gold = c.contracts[0]  # SELL before HOLD
    assert (gold.symbol, gold.action, gold.side, gold.strategies) == ("GOLD", "SELL", "short", ["channel", "trend"])
    assert (gold.prev_action, gold.prev_close) == ("WATCH", 100.0)
    assert alerts.commodity_summary(root, date="2026-11-23") is None


def test_commodity_card_and_caption(tmp_path):
    c = alerts.commodity_summary(commodities(tmp_path))
    page = alerts.commodity_card_html(c)
    assert '<span class="chip new">was WATCH</span>' in page and '<span class="chip down">▼2.0%</span>' in page
    assert ">HOLD LONG</span>" in page and '<span class="score neg">−0.40</span>' in page
    assert "70% technical + 30% news sentiment" in page
    assert alerts.commodity_caption(c, "https://example.org/") == (
        "🛢 <b>AI Commodities · Wed 25 Nov 2026</b>\n⚡ Gold SELL\n"
        '👉 <a href="https://example.org/#journal/commodities/days/2026-11-25.csv">Open this session</a>')


def test_commodities_go_to_telegram_as_a_second_message(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    sent = []
    monkeypatch.setattr(alerts, "card_png", lambda s, notes=True: [b"stocks"])
    monkeypatch.setattr(alerts, "commodity_png", lambda c: None)  # no image: text
    monkeypatch.setattr(alerts, "telegram_photo", lambda png, caption, token, chat: sent.append(png))
    monkeypatch.setattr(alerts, "telegram", lambda text, token, chat: sent.append(text.splitlines()[0]))
    root = commodities(journal(tmp_path))
    out = alerts.send(alerts.summary(root), to_issue=False, to_email=False, c=alerts.commodity_summary(root))
    assert out == ["Telegram: sent (card image)", "Telegram commodities: sent (text)"]
    assert sent == [[b"stocks"], "🛢 <b>AI Commodities · Wed 25 Nov 2026</b>"]


def test_commodities_alone_without_a_stock_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("SMTP_USERNAME", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    sent = []
    monkeypatch.setattr(alerts, "commodity_png", lambda c: None)
    monkeypatch.setattr(alerts, "telegram", lambda text, token, chat: sent.append(text.splitlines()[0]))
    out = alerts.send(None, c=alerts.commodity_summary(commodities(tmp_path)))
    assert out == ["Telegram commodities: sent (text)"]  # no stock card, email or issue
    assert sent == ["🛢 <b>AI Commodities · Wed 25 Nov 2026</b>"]


NCOLS = "date,symbol,name,strategy,action,note,source\n"


def test_notes_follow_each_card_for_the_stocks_and_contracts_it_shows(tmp_path, monkeypatch):
    root = commodities(journal(tmp_path))
    (root / "journal" / "notes").mkdir()
    (root / "journal" / "notes" / "2026-11-26.csv").write_text(
        NCOLS + "2026-11-26,C,Gamma,swing,BUY,C <rises>,live\n2026-11-26,C,Gamma,trend,BUY,C again,live\n"
                "2026-11-26,Z,Zeta,swing,BUY,not on the card,live\n")
    (root / "journal" / "commodities" / "notes").mkdir()
    (root / "journal" / "commodities" / "notes" / "2026-11-25.csv").write_text(
        NCOLS + "2026-11-25,GOLD,Gold,channel,SELL,Gold falls,live\n")
    s, c = alerts.summary(root), alerts.commodity_summary(root)
    assert [b.symbol for b in s.mentioned()] == ["C", "A"]  # new first, no repeats
    assert alerts.stock_notes(s) == [("C · Gamma · +0.60", "C <rises>")]  # first note per symbol; A has none
    assert alerts.commodity_notes(c) == [("Gold · SELL · −0.40", "Gold falls")]
    assert "C · Gamma · +0.60" in alerts.plain(s) and "<summary>C · Gamma" in alerts.markdown(s)
    sent = []
    monkeypatch.setattr(alerts, "card_png", lambda s, notes=True: [b"notes" if notes else b"card"])
    monkeypatch.setattr(alerts, "telegram_photo", lambda png, caption, token, chat: sent.append((png, caption)))
    monkeypatch.setattr(alerts, "telegram", lambda text, token, chat: sent.append(text))
    # the card alone, then the card with the notes as an album
    assert alerts._telegram(s, None, "t", "c") == "sent (card image + 1 notes album)"
    assert [png for png, _ in sent] == [[b"card"], [b"notes"]] and sent[1][1] == "🤖 <b>Notes · Thu 26 Nov 2026</b>"
    assert '<ul class="points"><li>C &lt;rises&gt;</li></ul>' in alerts.card_html(s)
    assert "points" not in alerts.card_html(s, notes=False).split("<body>")[1]
    sent.clear()
    monkeypatch.setattr(alerts, "card_png", lambda s, notes=True: None)  # no Chrome: the text card, then the notes as text
    assert alerts._telegram(s, None, "t", "c") == "sent (text) + 1 notes"
    assert sent[1] == ("🤖 <b>Notes · Thu 26 Nov 2026</b>\n\n<b>C · Gamma · +0.60</b>\n"
                       "<blockquote expandable>C &lt;rises&gt;</blockquote>")
    sent.clear()
    monkeypatch.setattr(alerts, "commodity_png", lambda c: [b"png"])
    assert alerts._telegram_commodities(c, None, "t", "c") == "sent (card image, 1 notes)" and len(sent) == 1
    assert "<li>Gold falls</li>" in alerts.commodity_card_html(c)


def test_notes_are_bullet_points_one_per_sentence():
    note = ("The strategy 'swing' fired a BUY signal for KPRMILL (K.P.R. Mill Limited) on an uptrend. "
            "The price is above its 200-day SMA. The stop is at 900.")
    assert alerts.points(note) == ["The strategy 'swing' fired a BUY signal for KPRMILL (K.P.R. Mill Limited) on an "
                                   "uptrend.", "The price is above its 200-day SMA.", "The stop is at 900."]
    assert alerts.points_html("Gold <falls>.") == '<ul class="points"><li>Gold &lt;falls&gt;.</li></ul>'


def test_notes_are_split_to_fit_telegram():
    messages = alerts.notes_html("Notes", [(f"S{i}", "x" * 900) for i in range(10)])
    assert len(messages) == 3 and all(len(m) <= alerts.TELEGRAM_LIMIT for m in messages)
    assert messages[1].startswith("🤖 <b>Notes</b> (cont.)") and alerts.notes_html("Notes", []) == []


def test_risk_points_are_red_and_target_points_green():
    note = "Uptrend above the SMA200. The main risk is a reversal. The target is 120. A stop at 90 limits the loss."
    assert [alerts.tone(x) for x in alerts.points(note)] == ["", "risk", "target", "risk"]
    assert alerts.points_html("Risky. Fine.") == '<ul class="points"><li class="risk">Risky.</li><li>Fine.</li></ul>'
