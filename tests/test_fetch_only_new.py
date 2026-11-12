"""LinkedIn detail pages are fetched only for rows that survive the filters.

Before: JobSpy fetched every result's page (~0.7 s each, plus delay) and the
known-URL check ran afterwards. On a live lane 99.7% of results were known or
filtered, so almost all of that time bought nothing.
"""

import pandas as pd

from jobmatch.database import init_db
from jobmatch.discovery import jobspy


def _df(*rows):
    return pd.DataFrame([{"job_url": u, "site": s, "title": "Front Desk", "description": d,
                          "job_url_direct": None, "location": "Bali"} for u, s, d in rows])


def test_known_urls_are_dropped_before_any_fetch(tmp_path):
    conn = init_db(tmp_path / "t.db")
    conn.execute("INSERT INTO jobs (url, status) VALUES ('https://www.linkedin.com/jobs/view/1', 'active')")
    df = _df(("https://www.linkedin.com/jobs/view/1", "linkedin", None),
             ("https://www.linkedin.com/jobs/view/2", "linkedin", None))

    kept, dropped = jobspy._drop_known_urls(conn, df)

    assert dropped == 1
    assert kept is not None
    assert list(kept["job_url"]) == ["https://www.linkedin.com/jobs/view/2"]


def test_details_fetched_only_for_linkedin_rows_without_text():
    df = _df(("https://www.linkedin.com/jobs/view/2", "linkedin", None),
             ("https://www.linkedin.com/jobs/view/3", "linkedin", "already have text"),
             ("https://id.indeed.com/viewjob?jk=4", "indeed", None))
    asked = []

    def fake_fetch(job_id):
        asked.append(job_id)
        return {"description": "x" * 300, "job_url_direct": "https://apply.example/2"}

    assert jobspy._fill_linkedin_details(df, fetch=fake_fetch) == 1
    assert asked == ["2"]
    assert df.loc[0, "description"] == "x" * 300
    assert df.loc[0, "job_url_direct"] == "https://apply.example/2"


def test_run_one_search_fetches_nothing_when_all_results_are_known(tmp_path, monkeypatch):
    conn = init_db(tmp_path / "t.db")
    conn.execute("INSERT INTO jobs (url, status) VALUES ('https://www.linkedin.com/jobs/view/1', 'active')")
    conn.commit()
    monkeypatch.setattr(jobspy, "get_connection", lambda: conn)
    seen_kwargs = []

    def fake_scrape(kwargs, max_retries=2):
        seen_kwargs.append(kwargs)
        return _df(("https://www.linkedin.com/jobs/view/1", "linkedin", None))

    monkeypatch.setattr(jobspy, "_scrape_with_retry", fake_scrape)

    def no_fetch():
        raise AssertionError("fetched a known job")

    monkeypatch.setattr(jobspy, "_linkedin_detail_fetcher", no_fetch)

    result = jobspy._run_one_search(
        {"query": "front office", "location": "Bali, Indonesia", "tier": 1}, ["linkedin"], 25, 168,
        None, {}, 0, ["Bali"], [], {},
    )

    assert (result["new"], result["existing"]) == (0, 1)
    assert "linkedin_fetch_description" not in seen_kwargs[0]
