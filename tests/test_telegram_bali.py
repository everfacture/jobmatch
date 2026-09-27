"""Tests for the Telegram Bali public-channel extractor.

Uses a captured HTML fixture rather than the network so the suite stays
deterministic and offline. The fixture is a trimmed real t.me/s/ page.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobmatch.discovery.smartextract import (  # noqa: E402
    _extract_telegram_bali,
    _telegram_job_title,
    _telegram_parse_posts,
    _telegram_query_relevant,
    _telegram_strip_html,
)


def _post(pid, date, body):
    return (
        f'<div class="tgme_widget_message_wrap">'
        f'<a class="tgme_widget_message_owner_name" href="#"></a>'
        f'<span data-post="carikerja_bali/{pid}"></span>'
        f'<time datetime="{date}T08:00:00+00:00"></time>'
        f'<div class="tgme_widget_message_text js-message_text">{body}</div>'
        f"</div>"
    )


PAGE = "<html><body>" + "".join([
    _post("33694", "2026-09-26", "Shop Assistant<br>Kirim CV ke: career@sensatia.com"),
    _post("33696", "2026-09-27", "HOUSEKEEPING TRAINEE<br>FRONT OFFICE TRAINEE"),
    _post("33690", "2026-09-26", "PR &amp; MARKETING OFFICER<br>SEND YOUR CV TO: x@y.com"),
    # numeric entity must be decoded
    _post("8727", "2026-09-22", "Kabar gembira&#33; Lowongan"),
    # junk: bare URL repost
    _post("8737", "2026-09-23", "https://www.linkedin.com/jobs/view/4462154203/"),
    # junk: chatter
    _post("8730", "2026-09-22", "kak mau kirim loker ya"),
]) + "</body></html>"


def test_parse_posts_extracts_ids_dates_and_text():
    posts = _telegram_parse_posts(PAGE)
    assert len(posts) == 6
    ids = [p[0] for p in posts]
    assert "33694" in ids and "8737" in ids
    dates = dict((p[0], p[1]) for p in posts)
    assert dates["33694"] == "2026-09-26"
    assert dates["33696"] == "2026-09-27"


def test_strip_html_decodes_numeric_and_named_entities():
    # Regression: a hand-rolled named-entity table left &#33; in the text.
    assert "!" in _telegram_strip_html("Kabar gembira&#33; Lowongan")
    assert "&" in _telegram_strip_html("PR &amp; MARKETING")
    assert "<br>" not in _telegram_strip_html("a<br>b")


def test_title_extraction_keeps_real_roles():
    assert _telegram_job_title("Shop Assistant\nKirim CV ke: x@y.com") == "Shop Assistant"
    assert _telegram_job_title("HOUSEKEEPING TRAINEE\nFRONT OFFICE TRAINEE") == "HOUSEKEEPING TRAINEE"
    assert "MARKETING" in _telegram_job_title("PR &amp; MARKETING OFFICER")


def test_title_extraction_rejects_bare_url_and_chatter():
    # Regression: these were being stored as job titles.
    assert _telegram_job_title("https://www.linkedin.com/jobs/view/4462154203/") == ""
    assert _telegram_job_title("kak mau kirim loker ya") == ""


def test_relevance_filter_requires_non_generic_anchor():
    body = "FRONT OFFICE TRAINEE"
    assert _telegram_query_relevant("FRONT OFFICE TRAINEE", body, "front office") is True
    assert _telegram_query_relevant("Florist Bali", "Florist Bali", "florist") is True
    # A query made entirely of generic/stop words ("admin", "staff") leaves no
    # anchor, so the filter deliberately fails OPEN and lets the scorer decide
    # rather than silently dropping every admin post in the lane.
    assert _telegram_query_relevant("Admin", "Admin wanted", "admin") is True
    # A specialist query must not match unrelated posts.
    assert _telegram_query_relevant("Housekeeping Trainee", "HOUSEKEEPING", "florist") is False


def test_extractor_end_to_end_offline(monkeypatch):
    """Full extractor against a stubbed HTTP layer."""
    import jobmatch.discovery.smartextract as st

    class FakeResp:
        status_code = 200
        text = PAGE

    monkeypatch.setenv("TELEGRAM_BALI_ENABLED", "true")
    monkeypatch.setenv("JOBMATCH_TELEGRAM_CHANNELS", "carikerja_bali")
    monkeypatch.setattr(st.httpx, "get", lambda *a, **k: FakeResp())

    jobs = _extract_telegram_bali("Telegram Bali", "https://t.me/s/carikerja_bali")
    titles = [j["title"] for j in jobs]

    # the two junk posts must not appear
    assert not any(t.startswith("https://") for t in titles)
    assert "kak mau kirim loker ya" not in titles
    # real roles survive
    assert "Shop Assistant" in titles
    assert "HOUSEKEEPING TRAINEE" in titles
    # permalinks are the stable dedupe identity
    assert all(j["url"].startswith("https://t.me/carikerja_bali/") for j in jobs)
    assert len({j["url"] for j in jobs}) == len(jobs)
    # Bali-tagged so the profile location gate keeps them
    assert all(j["location"] == "Bali, Indonesia" for j in jobs)


def test_extracted_job_carries_date_posted(monkeypatch):
    """Freshness signal must survive extraction, not just parsing."""
    import jobmatch.discovery.smartextract as st

    class FakeResp:
        status_code = 200
        text = PAGE

    monkeypatch.setenv("TELEGRAM_BALI_ENABLED", "true")
    monkeypatch.setenv("JOBMATCH_TELEGRAM_CHANNELS", "carikerja_bali")
    monkeypatch.setattr(st.httpx, "get", lambda *a, **k: FakeResp())

    jobs = _extract_telegram_bali("Telegram Bali", "https://t.me/s/carikerja_bali")
    by_url = {j["url"]: j for j in jobs}
    shop = by_url["https://t.me/carikerja_bali/33694"]
    assert shop["date_posted"] == "2026-09-26", "post date must be carried for staleness checks"


def test_channels_fetched_once_per_process_not_per_query(monkeypatch):
    """Regression: run_smart_extract() calls each extractor once per query.

    Fetching inside that loop turned a 46-query profile into ~92 Telegram
    requests, stalling discovery for minutes. The channel fetch must be cached
    per process so a full run costs one request per channel.
    """
    import jobmatch.discovery.smartextract as st

    calls = []

    class FakeResp:
        status_code = 200
        text = PAGE

    def counting_get(*a, **k):
        calls.append(a[0] if a else k.get("url"))
        return FakeResp()

    st._TELEGRAM_POST_CACHE.clear()
    monkeypatch.setenv("TELEGRAM_BALI_ENABLED", "true")
    monkeypatch.setenv("JOBMATCH_TELEGRAM_CHANNELS", "carikerja_bali,loker_bali")
    monkeypatch.setattr(st.httpx, "get", counting_get)

    queries = ["admin", "front office", "staf administrasi", "resepsionis",
               "personal assistant", "reservation", "kasir", "arsip"]
    total = 0
    for q in queries:
        total += len(_extract_telegram_bali("Telegram Bali", "", q))

    # 2 channels, not 2 x len(queries)
    assert len(calls) == 2, f"expected one fetch per channel, got {len(calls)}: {calls}"
    assert total > 0, "cached run must still return jobs"
    st._TELEGRAM_POST_CACHE.clear()


def test_extractor_respects_disable_switch(monkeypatch):
    import jobmatch.discovery.smartextract as st

    def boom(*a, **k):
        raise AssertionError("must not fetch when disabled")

    monkeypatch.setenv("TELEGRAM_BALI_ENABLED", "false")
    monkeypatch.setattr(st.httpx, "get", boom)
    assert _extract_telegram_bali("Telegram Bali", "https://t.me/s/carikerja_bali") == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
