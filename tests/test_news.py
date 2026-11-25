from datetime import date, datetime, timedelta, timezone

from aistock.data import news


def ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_mentions_matches_name_words_or_upper_case_symbol():
    assert news.mentions("Infosys wins $1 bn deal", "Infosys", "INFY")
    assert news.mentions("HDFC BANK shares rise", "HDFC Bank", "HDFCBANK")
    assert news.mentions("TCS Q2 results beat estimates", "Tata Consultancy Services", "TCS")
    assert not news.mentions("Infosystems unit sold", "Infosys", "INFY")  # whole words only
    assert news.mentions("itc tweaks its logo", "ITC", "ITC")  # name match ignores case
    assert not news.mentions("Bel Air index up", "Bharat Electronics", "BEL")  # symbol must be upper case
    assert not news.mentions("LT bonds", "Larsen & Toubro", "LT")  # 2-letter symbols too ambiguous


def test_get_news_merges_publishers_dedupes_and_drops_old(tmp_path, monkeypatch):
    monkeypatch.setattr(news, "CACHE", tmp_path)
    monkeypatch.setattr(news, "_google_news", lambda q, days: [
        {"title": "Infosys wins deal", "source": "Mint", "published": ago(1), "link": ""}])
    monkeypatch.setattr(news, "_yahoo_news", lambda s, k: [])
    monkeypatch.setattr(news, "_publisher_items", lambda: (
        {"title": "Infosys wins deal", "source": "Mint", "published": ago(1), "link": ""},  # same as Google's
        {"title": "INFY slips after guidance", "source": "BusinessLine", "published": ago(0.1), "link": ""},
        {"title": "Infosys AGM recap", "source": "Economic Times", "published": ago(30), "link": ""},  # too old
        {"title": "Wipro rallies", "source": "Economic Times", "published": ago(0.2), "link": ""},  # other stock
    ))
    got = news.get_news("INFY", "Infosys Limited", days=14)
    assert [n["title"] for n in got] == ["INFY slips after guidance", "Infosys wins deal"]


def test_get_news_as_of_limits_google_to_the_window_and_cuts_at_the_session(tmp_path, monkeypatch):
    monkeypatch.setattr(news, "CACHE", tmp_path)
    asked = []

    def fake(query):
        asked.append(query)
        return [{"title": "Infosys wins deal", "source": "Mint", "published": "2026-11-17T05:00:00+00:00", "link": ""},
                {"title": "Infosys after hours", "source": "Mint", "published": "2026-11-18T15:00:00+00:00", "link": ""}]
    monkeypatch.setattr(news, "_google_news_query", fake)
    got = news.get_news_as_of("INFY", "Infosys Limited", date(2026, 11, 18), days=14)
    assert asked == ['"Infosys" share after:2026-11-04 before:2026-11-19']
    assert [n["title"] for n in got] == ["Infosys wins deal"]  # 20:30 IST is after the 17:30 IST cutoff
