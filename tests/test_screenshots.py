import json

from aistock.site import screenshots as shots


def test_pages_from_body_reads_the_marker_line():
    body = "Intro\n<!-- screenshots: today journal/stocks/INFY.csv?range=1M -->\n## Test plan"
    assert shots.pages_from_body(body) == ["today", "journal/stocks/INFY.csv?range=1M"]
    assert shots.pages_from_body("no marker") == []
    assert shots.pages_from_body(None) == []


def test_default_pages_take_latest_session_and_a_held_stock(tmp_path):
    data = tmp_path / "data"
    (data / "journal" / "paper" / "trend").mkdir(parents=True)
    (data / "journal" / "stocks").mkdir(parents=True)
    (data / "manifest.json").write_text(json.dumps({"files": [
        {"path": "journal/days/2026-11-25.csv", "kind": "day"},
        {"path": "journal/days/2026-11-26.csv", "kind": "day"}]}))
    (data / "journal" / "paper" / "trend" / "positions.csv").write_text("entry_date,sym,shares\n2026-11-19,INFY,4\n")
    (data / "journal" / "stocks" / "INFY.csv").write_text("date,symbol\n")

    assert shots.default_pages(tmp_path) == ["today", "accounts", "journal/days/2026-11-26.csv",
                                             "journal/stocks/INFY.csv?range=1M"]


def test_file_name_is_stable_and_flat():
    assert shots.file_name("journal/stocks/M&M.csv?range=1M") == "journal-stocks-M-M-csv-range-1M.png"
    assert shots.file_name("today") == "today.png"


def test_replace_section_swaps_the_old_block_or_inserts_before_test_plan():
    block = f"{shots.SECTION_START}\nnew\n{shots.SECTION_END}"
    old = f"Summary\n\n{shots.SECTION_START}\nold\n{shots.SECTION_END}\n\n## Test plan\n- [x] ok"
    assert shots.replace_section(old, block) == f"Summary\n\n{block}\n\n## Test plan\n- [x] ok"
    assert shots.replace_section("Summary\n\n## Test plan\n- x", block) == f"Summary\n\n{block}\n\n## Test plan\n- x"
    assert shots.replace_section("Summary", block) == f"Summary\n\n{block}\n"


def test_section_links_each_page_in_order():
    block = shots.section([("today", "https://x/today.png"), ("accounts", "https://x/accounts.png")],
                          "abcdef1234", ["today", "accounts"])
    assert block.startswith(shots.SECTION_START) and block.endswith(shots.SECTION_END)
    assert "from abcdef1" in block
    assert block.index("![today](https://x/today.png)") < block.index("![accounts](https://x/accounts.png)")


def test_pages_from_body_ignores_quoted_examples_and_the_bot_section():
    quoted = "Pick pages with a `<!-- screenshots: ... -->` line."
    assert shots.pages_from_body(quoted) == []
    bot = shots.section([("today", "https://x/today.png")], "abc", ["today"])
    assert shots.pages_from_body(f"Summary\n\n{bot}\n") == []
    assert shots.pages_from_body("<!-- screenshots: ... accounts -->") == ["accounts"]
