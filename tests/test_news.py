from datetime import datetime, timedelta, timezone

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
